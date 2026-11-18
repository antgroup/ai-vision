package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/config"
	"github.com/antgroup/ai-vision/aivision-cli/internal/query"
)

// cmdQuery routes: query trace|span|session|log|metric|metadata <sub> [flags].
//
// Every sub-command talks to the aivision OpenAPI with the active SpaceKey; the
// space and agent product scope the path. Result visibility is enforced by the
// backend, so the CLI does no client-side authorization.
func cmdQuery(args []string) error {
	if len(args) == 0 {
		fmt.Fprint(os.Stderr, queryUsage)
		return fmt.Errorf("query: missing signal (trace|span|session|log|metric|metadata)")
	}
	signal, rest := args[0], args[1:]
	switch signal {
	case "trace":
		return queryTrace(rest)
	case "span":
		return querySpan(rest)
	case "session":
		return querySession(rest)
	case "log":
		return queryLog(rest)
	case "metric":
		return queryMetric(rest)
	case "metadata":
		return queryMetadata(rest)
	case "help", "-h", "--help":
		fmt.Print(queryUsage)
		return nil
	default:
		fmt.Fprint(os.Stderr, queryUsage)
		return fmt.Errorf("query: unknown signal %q (want trace|span|session|log|metric|metadata)", signal)
	}
}

// --- shared flag plumbing ----------------------------------------------------

type commonFlags struct {
	endpoint string
	spaceKey string
	space    string
	product  string
	profile  string
	format   string
	outFile  string
	since    string
	until    string
	lastN    int
	limit    int
	page     int
	orderBy  string
	fields   string
	filter   string
	params   varFlags
	noTime   bool
}

func registerCommon(fs *flag.FlagSet) *commonFlags {
	return registerQueryFlags(fs, true)
}

func registerQueryFlags(fs *flag.FlagSet, listOptions bool) *commonFlags {
	c := &commonFlags{}
	fs.StringVar(&c.profile, "profile", os.Getenv(config.EnvProfile), "saved query profile (default: $"+config.EnvProfile+" or active profile)")
	fs.StringVar(&c.endpoint, "endpoint", "", "AI Vision base URL (default: selected profile; $"+config.EnvEndpoint+" when no profile is saved)")
	fs.StringVar(&c.spaceKey, "space-key", "", "SpaceKey (default: selected space's saved key; $"+config.EnvSpaceKey+" without a saved profile)")
	fs.StringVar(&c.space, "space", "", "space id (default: selected profile; $"+config.EnvSpaceID+" without a saved profile)")
	fs.StringVar(&c.product, "agent-product", "", "agent product (default: selected space's last connected Agent; $"+config.EnvAgentProduct+" without a saved profile)")
	defaultFormat, formatHelp := "table", "output format: table|json"
	if !listOptions {
		defaultFormat, formatHelp = "json", "output format: json (complete aggregate object)"
	}
	fs.StringVar(&c.format, "format", defaultFormat, formatHelp)
	fs.StringVar(&c.format, "output", defaultFormat, "alias for --format")
	fs.StringVar(&c.outFile, "o", "", "write output to FILE instead of stdout")
	fs.StringVar(&c.outFile, "out", "", "write output to FILE instead of stdout")
	fs.StringVar(&c.since, "since", "", "start time: RFC3339, epoch ms/s, or relative (90m,6h,2d). default: 1h ago")
	fs.StringVar(&c.until, "until", "", "end time (same forms). default: now")
	fs.IntVar(&c.lastN, "last-n-minutes", 0, "convenience for --since <N>m --until now")
	if listOptions {
		fs.IntVar(&c.limit, "limit", 20, "max rows")
		fs.IntVar(&c.limit, "n", 20, "alias for --limit")
		fs.IntVar(&c.page, "page", 0, "page number (1-based) when the endpoint paginates")
		fs.StringVar(&c.orderBy, "order-by", "", "order field (endpoint default when empty)")
		fs.StringVar(&c.fields, "fields", "", "comma-separated fields to select (endpoint default when empty)")
	}
	fs.StringVar(&c.filter, "filter", "", "filter expression or JSON, sent as `filters`")
	fs.Var(&c.params, "param", "extra body/query param k=v (repeatable; value parsed as JSON when possible)")
	return c
}

// client resolves flags and scoped profile values, or environment values when
// no profile is saved, into a query.Client.
func (c *commonFlags) client() (*query.Client, error) {
	endpoint, spaceKey, space, product := c.endpoint, c.spaceKey, c.space, c.product
	cfg, err := config.Load()
	if err != nil {
		return nil, err
	}
	p, ok := cfg.Get(c.profile)
	if c.profile != "" && !ok {
		return nil, fmt.Errorf("profile %q not found", c.profile)
	}
	if ok {
		endpoint = firstNonEmpty(endpoint, p.Endpoint)
		space = firstNonEmpty(space, p.SpaceID)
		// Explicit space/endpoint overrides must never inherit another scope's
		// secret or fall back to stale exports from the parent shell.
		if strings.TrimRight(endpoint, "/") == strings.TrimRight(p.Endpoint, "/") {
			s := p.Spaces[space]
			spaceKey = firstNonEmpty(spaceKey, s.SpaceKey)
			product = firstNonEmpty(product, s.AgentProduct)
		}
	} else {
		endpoint = firstNonEmpty(endpoint, os.Getenv(config.EnvEndpoint))
		space = firstNonEmpty(space, os.Getenv(config.EnvSpaceID))
		if (c.endpoint == "" || c.endpoint == os.Getenv(config.EnvEndpoint)) && (c.space == "" || c.space == os.Getenv(config.EnvSpaceID)) {
			spaceKey = firstNonEmpty(spaceKey, os.Getenv(config.EnvSpaceKey))
			product = firstNonEmpty(product, os.Getenv(config.EnvAgentProduct))
		}
	}

	if endpoint == "" {
		return nil, fmt.Errorf("no endpoint: pass --endpoint, set %s, or run: aiv auth login", config.EnvEndpoint)
	}
	if spaceKey == "" {
		return nil, fmt.Errorf("no query key for this space: run aiv key create --name cli or aiv key import --key-stdin (or pass --space-key)")
	}
	if space == "" {
		return nil, fmt.Errorf("no space id: pass --space, set %s, or run: aiv auth login", config.EnvSpaceID)
	}
	if product == "" {
		return nil, fmt.Errorf("no agent product: pass --agent-product, set %s, or run: aiv auth login", config.EnvAgentProduct)
	}
	return query.New(endpoint, spaceKey, space, product), nil
}

// timeRange resolves --since/--until/--last-n-minutes to epoch ms. It defaults
// to the last hour when nothing is supplied.
func (c *commonFlags) timeRange() (int64, int64, error) {
	now := time.Now()
	if c.lastN > 0 {
		return now.Add(-time.Duration(c.lastN) * time.Minute).UnixMilli(), now.UnixMilli(), nil
	}
	return query.ResolveRange(c.since, c.until, now)
}

// body assembles a POST body from the common flags plus --param passthrough.
func (c *commonFlags) body() (map[string]any, error) {
	b := map[string]any{}
	if !c.noTime {
		start, end, err := c.timeRange()
		if err != nil {
			return nil, err
		}
		b["from"] = start
		b["to"] = end
	}
	if c.limit > 0 {
		b["limit"] = c.limit
	}
	if c.page > 0 {
		b["page"] = c.page
	}
	if strings.TrimSpace(c.orderBy) != "" {
		b["orderBy"] = c.orderBy
	}
	if fields := splitCSV(c.fields); len(fields) > 0 {
		b["fields"] = fields
	}
	if strings.TrimSpace(c.filter) != "" {
		b["filters"] = parseValue(c.filter)
	}
	if err := applyParams(b, c.params); err != nil {
		return nil, err
	}
	return b, nil
}

// query builds url.Values from the common time range plus --param passthrough
// (for GET endpoints, which take query-string params).
func (c *commonFlags) query() (url.Values, error) {
	q := url.Values{}
	if !c.noTime {
		start, end, err := c.timeRange()
		if err != nil {
			return nil, err
		}
		q.Set("from", strconv.FormatInt(start, 10))
		q.Set("to", strconv.FormatInt(end, 10))
	}
	if c.limit > 0 {
		q.Set("limit", strconv.Itoa(c.limit))
	}
	for _, kv := range c.params {
		i := strings.IndexByte(kv, '=')
		if i <= 0 {
			return nil, fmt.Errorf("invalid --param %q (want k=v)", kv)
		}
		q.Set(kv[:i], kv[i+1:])
	}
	return q, nil
}

func activeProfile() (config.Profile, bool) {
	cfg, err := config.Load()
	if err != nil {
		return config.Profile{}, false
	}
	return cfg.Get(os.Getenv(config.EnvProfile))
}

// --- trace -------------------------------------------------------------------

func queryTrace(args []string) error {
	sub, rest := splitSub(args)
	switch sub {
	case "list":
		return runList(rest, "traces/list")
	case "get":
		return queryTraceGet(rest)
	case "export":
		return queryTraceExport(rest)
	case "metrics":
		return queryTraceMetrics(rest)
	case "help", "-h", "--help":
		fmt.Print(queryUsage)
		return nil
	default:
		return fmt.Errorf("query trace: unknown sub-command %q (want list|get|export|metrics)", sub)
	}
}

func queryTraceMetrics(args []string) error {
	fs := flag.NewFlagSet("query trace metrics", flag.ContinueOnError)
	c := registerQueryFlags(fs, false)
	fs.Lookup("filter").Usage = "JSON array of field/op/value conditions (IN uses a value array)"
	fs.Lookup("param").Usage = "extra supported param k=v: from, to, filters, agent_uuid, bucket, groupBy"
	agentUUID := fs.String("agent-uuid", "", "optional Agent instance UUID within the selected profile scope")
	bucket := fs.String("bucket", "hour", "timeseries bucket: minute|hour|day")
	groupBy := fs.String("group-by", "", "optional grouping: model|provider")
	fs.Usage = func() {
		fmt.Fprintln(fs.Output(), "Usage: aiv query trace metrics [flags]\nAggregate the full selected time window. No list pagination; scans over 10000 traces fail.\nResults require meta.complete=true. Missing usage stays null with coverage.")
		fs.PrintDefaults()
	}
	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() != 0 {
		return fmt.Errorf("query trace metrics takes flags only, not positional IDs")
	}
	if !strings.EqualFold(c.format, "json") {
		return fmt.Errorf("query trace metrics requires --format json to preserve totals and coverage")
	}
	body, err := c.body()
	if err != nil {
		return err
	}
	for k := range body {
		if !inList([]string{"from", "to", "filters", "agent_uuid", "bucket", "groupBy"}, k) {
			return fmt.Errorf("query trace metrics: unsupported parameter %q", k)
		}
	}
	if _, ok := body["bucket"]; !ok {
		body["bucket"] = *bucket
	}
	if *groupBy != "" {
		body["groupBy"] = *groupBy
	}
	if *agentUUID != "" {
		body["agent_uuid"] = *agentUUID
	}
	if v, ok := body["bucket"].(string); !ok || !inList([]string{"minute", "hour", "day"}, v) {
		return fmt.Errorf("query trace metrics: bucket must be minute, hour or day")
	}
	if raw, exists := body["groupBy"]; exists {
		if v, ok := raw.(string); !ok || !inList([]string{"model", "provider"}, v) {
			return fmt.Errorf("query trace metrics: groupBy must be model or provider")
		}
	}
	if raw, exists := body["filters"]; exists {
		if _, ok := raw.([]any); !ok {
			return fmt.Errorf("query trace metrics: filters must be a JSON array of field/op/value conditions")
		}
	}
	cl, err := c.client()
	if err != nil {
		return err
	}
	data, err := cl.Post(context.Background(), cl.AgentURL("traces/metrics"), body)
	if err != nil {
		return err
	}
	var result struct {
		Meta struct {
			Complete bool `json:"complete"`
		} `json:"meta"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return fmt.Errorf("query trace metrics: invalid aggregate response: %w", err)
	}
	if !result.Meta.Complete {
		return fmt.Errorf("query trace metrics: response requires meta.complete=true; partial aggregates were not written")
	}
	return c.emit(data)
}

func queryTraceGet(args []string) error {
	fs := flag.NewFlagSet("query trace get", flag.ContinueOnError)
	c := registerCommon(fs)
	c.noTime = true
	leading, rest := splitLeadingPos(args)
	if err := fs.Parse(rest); err != nil {
		return err
	}
	pos := append(leading, fs.Args()...)
	if len(pos) < 1 {
		return fmt.Errorf("usage: aiv query trace get <trace-id> [flags]")
	}
	cl, err := c.client()
	if err != nil {
		return err
	}
	q, err := c.query()
	if err != nil {
		return err
	}
	data, err := cl.Get(context.Background(), cl.AgentURL("trace/"+url.PathEscape(pos[0])+"/detail"), q)
	if err != nil {
		return err
	}
	return c.emit(data)
}

// queryTraceExport lists traces then writes each trace's detail as JSONL (or one
// file per trace when --filename-pattern contains {trace_id}).
func queryTraceExport(args []string) error {
	fs := flag.NewFlagSet("query trace export", flag.ContinueOnError)
	c := registerCommon(fs)
	pattern := fs.String("filename-pattern", "", "per-trace file name; {trace_id} is substituted (default: single traces.jsonl)")
	leading, rest := splitLeadingPos(args)
	if err := fs.Parse(rest); err != nil {
		return err
	}
	pos := append(leading, fs.Args()...)
	if len(pos) < 1 {
		return fmt.Errorf("usage: aiv query trace export <dir> [--limit N] [--filename-pattern '{trace_id}.json']")
	}
	dir := pos[0]
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	cl, err := c.client()
	if err != nil {
		return err
	}
	body, err := c.body()
	if err != nil {
		return err
	}
	data, err := cl.Post(context.Background(), cl.AgentURL("traces/list"), body)
	if err != nil {
		return err
	}
	rows := query.ExtractRows(data)
	if len(rows) == 0 {
		fmt.Fprintln(os.Stderr, "(no traces to export)")
		return nil
	}
	ctx := context.Background()
	// Reuse the list's frozen bounds and route, without its projection, filters
	// or pagination. A detail read may otherwise select another Agent/backend
	// or use a recent default window that excludes the listed trace.
	detailParams := url.Values{}
	for _, key := range []string{"agent_uuid", "sourceSystem", "source_system", "from", "to"} {
		if value, ok := body[key]; ok {
			detailParams.Set(key, stringify(value))
		}
	}
	readDetail := func(row query.Row, exported int, location string) (json.RawMessage, error) {
		id := rowID(row, "trace_id", "traceId", "id")
		incomplete := func(err error) (json.RawMessage, error) {
			return nil, fmt.Errorf("trace export incomplete for trace %q; %d complete trace(s) retained at %s: %w", id, exported, location, err)
		}
		if id == "" {
			return incomplete(fmt.Errorf("list row has no trace ID"))
		}
		detail, err := cl.Get(ctx, cl.AgentURL("trace/"+url.PathEscape(id)+"/detail"), detailParams)
		if err != nil {
			return incomplete(err)
		}
		var object query.Row
		if err := json.Unmarshal(detail, &object); err != nil || len(object) == 0 {
			return incomplete(fmt.Errorf("detail response is missing or invalid"))
		}
		if err := validateTraceExportDetail(row, object); err != nil {
			return incomplete(err)
		}
		return detail, nil
	}
	if strings.Contains(*pattern, "{trace_id}") {
		n := 0
		for _, r := range rows {
			id := rowID(r, "trace_id", "traceId", "id")
			detail, err := readDetail(r, n, dir)
			if err != nil {
				return err
			}
			name := strings.ReplaceAll(*pattern, "{trace_id}", id)
			path, err := safeJoin(dir, name)
			if err != nil {
				return err
			}
			if err := writeJSONFile(path, detail); err != nil {
				return err
			}
			n++
		}
		fmt.Printf("exported %d trace(s) to %s\n", n, dir)
		return nil
	}
	out := filepath.Join(dir, "traces.jsonl")
	f, err := os.Create(out)
	if err != nil {
		return err
	}
	defer f.Close()
	n := 0
	for _, r := range rows {
		detail, err := readDetail(r, n, out)
		if err != nil {
			return err
		}
		if _, err := f.Write(append(compactJSON(detail), '\n')); err != nil {
			return err
		}
		n++
	}
	fmt.Printf("exported %d trace(s) to %s\n", n, out)
	return nil
}

func validateTraceExportDetail(row, detail query.Row) error {
	id := rowID(row, "trace_id", "traceId", "id")
	if rowID(detail, "traceId", "trace_id", "id") != id {
		return fmt.Errorf("detail trace ID does not match the listed trace")
	}
	if meta, ok := detail["meta"].(map[string]any); ok {
		if complete, exists := meta["complete"]; exists && complete != true {
			return fmt.Errorf("detail response is explicitly incomplete")
		}
	}

	rawSpans, native := detail["spans"]
	expected, hasCount := row["span_count"]
	if !native && !hasCount {
		// The legacy Langfuse adapter returns official id/observations, without
		// native span_count/spans. Do not impose the native representation on it.
		if _, ok := detail["observations"].([]any); !ok {
			return fmt.Errorf("detail response has no spans or observations")
		}
		return nil
	}
	spans, ok := rawSpans.([]any)
	if !ok || len(spans) == 0 {
		return fmt.Errorf("native detail has missing or empty spans")
	}
	ids := make(map[string]bool, len(spans))
	for _, value := range spans {
		span, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("native detail contains an invalid span")
		}
		spanID, ok := span["span_id"].(string)
		if !ok || strings.TrimSpace(spanID) == "" || ids[spanID] {
			return fmt.Errorf("native detail contains a missing or duplicate span ID")
		}
		ids[spanID] = true
		if traceID, exists := span["trace_id"]; exists && traceID != id {
			return fmt.Errorf("native span trace ID does not match the listed trace")
		}
	}
	if hasCount {
		count, err := strconv.ParseUint(stringify(expected), 10, 64)
		if err != nil || count != uint64(len(ids)) {
			return fmt.Errorf("native detail span count does not match the listed span_count")
		}
	}
	return nil
}

// --- span --------------------------------------------------------------------

func querySpan(args []string) error {
	sub, rest := splitSub(args)
	switch sub {
	case "list":
		return querySpanList(rest)
	case "get":
		return querySpanGet(rest)
	case "help", "-h", "--help":
		fmt.Print(queryUsage)
		return nil
	default:
		return fmt.Errorf("query span: unknown sub-command %q (want list|get)", sub)
	}
}

// querySpanList posts trace/{id}/spans with the given span ids (batch endpoint).
func querySpanList(args []string) error {
	fs := flag.NewFlagSet("query span list", flag.ContinueOnError)
	c := registerCommon(fs)
	c.noTime = true
	leading, rest := splitLeadingPos(args)
	if err := fs.Parse(rest); err != nil {
		return err
	}
	pos := append(leading, fs.Args()...)
	if len(pos) < 1 {
		return fmt.Errorf("usage: aiv query span list <trace-id> [span-id...] [flags]")
	}
	traceID := pos[0]
	spanIDs := pos[1:]
	if len(spanIDs) == 0 {
		return fmt.Errorf("query span list: the spans endpoint is batch-by-id; pass at least one <span-id> (or use: query trace get %s)", traceID)
	}
	cl, err := c.client()
	if err != nil {
		return err
	}
	body := map[string]any{"spanIds": spanIDs}
	if err := applyParams(body, c.params); err != nil {
		return err
	}
	data, err := cl.Post(context.Background(), cl.AgentURL("trace/"+url.PathEscape(traceID)+"/spans"), body)
	if err != nil {
		return err
	}
	return c.emit(data)
}

func querySpanGet(args []string) error {
	fs := flag.NewFlagSet("query span get", flag.ContinueOnError)
	c := registerCommon(fs)
	c.noTime = true
	flags, pos := splitInterspersedFlags(fs, args)
	if err := fs.Parse(flags); err != nil {
		return err
	}
	if len(pos) != 2 {
		return fmt.Errorf("usage: aiv query span get <trace-id> <span-id> [flags]")
	}
	cl, err := c.client()
	if err != nil {
		return err
	}
	q, err := c.query()
	if err != nil {
		return err
	}
	path := "trace/" + url.PathEscape(pos[0]) + "/span/" + url.PathEscape(pos[1])
	data, err := cl.Get(context.Background(), cl.AgentURL(path), q)
	if err != nil {
		return err
	}
	return c.emit(data)
}

// --- session -----------------------------------------------------------------

func querySession(args []string) error {
	sub, rest := splitSub(args)
	switch sub {
	case "list":
		return runList(rest, "sessions/list")
	case "get":
		return querySessionGet(rest)
	case "io":
		return querySessionIO(rest)
	case "usage":
		return querySessionUsage(rest)
	case "help", "-h", "--help":
		fmt.Print(queryUsage)
		return nil
	default:
		return fmt.Errorf("query session: unknown sub-command %q (want list|get|io|usage)", sub)
	}
}

func querySessionGet(args []string) error {
	fs := flag.NewFlagSet("query session get", flag.ContinueOnError)
	c := registerCommon(fs)
	c.noTime = true
	leading, rest := splitLeadingPos(args)
	if err := fs.Parse(rest); err != nil {
		return err
	}
	pos := append(leading, fs.Args()...)
	if len(pos) < 1 {
		return fmt.Errorf("usage: aiv query session get <session-id> [flags]")
	}
	cl, err := c.client()
	if err != nil {
		return err
	}
	q, err := c.query()
	if err != nil {
		return err
	}
	data, err := cl.Get(context.Background(), cl.AgentURL("session/"+url.PathEscape(pos[0])+"/detail"), q)
	if err != nil {
		return err
	}
	return c.emit(data)
}

func querySessionIO(args []string) error {
	fs := flag.NewFlagSet("query session io", flag.ContinueOnError)
	c := registerCommon(fs)
	c.noTime = true
	leading, rest := splitLeadingPos(args)
	if err := fs.Parse(rest); err != nil {
		return err
	}
	pos := append(leading, fs.Args()...)
	if len(pos) < 1 {
		return fmt.Errorf("usage: aiv query session io <session-id> [flags]")
	}
	cl, err := c.client()
	if err != nil {
		return err
	}
	body, err := c.body()
	if err != nil {
		return err
	}
	data, err := cl.Post(context.Background(), cl.AgentURL("session/"+url.PathEscape(pos[0])+"/io"), body)
	if err != nil {
		return err
	}
	return c.emit(data)
}

// querySessionUsage posts sessions/usage with a batch of session ids.
func querySessionUsage(args []string) error {
	fs := flag.NewFlagSet("query session usage", flag.ContinueOnError)
	c := registerCommon(fs)
	flags, pos := splitInterspersedFlags(fs, args)
	if err := fs.Parse(flags); err != nil {
		return err
	}
	if len(pos) < 1 {
		return fmt.Errorf("usage: aiv query session usage <session-id>... [flags]")
	}
	cl, err := c.client()
	if err != nil {
		return err
	}
	body, err := c.body()
	if err != nil {
		return err
	}
	body["sessionIds"] = pos
	data, err := cl.Post(context.Background(), cl.AgentURL("sessions/usage"), body)
	if err != nil {
		return err
	}
	return c.emit(data)
}

// --- log (events) ------------------------------------------------------------

func queryLog(args []string) error {
	sub, rest := splitSub(args)
	switch sub {
	case "list":
		return runList(rest, "events/list", "events.list")
	case "query":
		return queryLogQuery(rest)
	case "export":
		return queryLogExport(rest)
	case "help", "-h", "--help":
		fmt.Print(queryUsage)
		return nil
	default:
		return fmt.Errorf("query log: unknown sub-command %q (want list|query|export)", sub)
	}
}

// queryLogQuery posts events/query in fieldsJson mode (filter passthrough).
func queryLogQuery(args []string) error {
	fs := flag.NewFlagSet("query log query", flag.ContinueOnError)
	c := registerCommon(fs)
	mode := fs.String("mode", "fieldsJson", "query mode: fieldsJson|rawSql")
	sql := fs.String("sql", "", "raw SQL (only for --mode rawSql)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *mode == "rawSql" && strings.TrimSpace(*sql) == "" {
		return fmt.Errorf("query log query: --sql is required for --mode rawSql")
	}
	cl, err := c.client()
	if err != nil {
		return err
	}
	var body map[string]any
	if *mode == "rawSql" {
		body, err = rawSQLBody(c)
		if err != nil {
			return err
		}
		body["mode"] = *mode
		body["rawSql"] = *sql
	} else {
		body, err = c.body()
		if err != nil {
			return err
		}
		body["mode"] = *mode
	}
	if err := requireEventQueryMode(context.Background(), cl, *mode, body); err != nil {
		return err
	}
	data, err := cl.Post(context.Background(), cl.AgentURL("events/query"), body)
	if err != nil {
		return err
	}
	return c.emit(data)
}

// rawSQLAllowedBodyFields mirrors the backend's _RAW_SQL_ALLOWED_BODY_FIELDS.
// Raw SQL must never carry pagination, filtering, or other server-controlled
// fields assembled by commonFlags.body.
var rawSQLAllowedBodyFields = map[string]bool{
	"mode":          true,
	"rawSql":        true,
	"raw_sql":       true,
	"refId":         true,
	"ref_id":        true,
	"format":        true,
	"from":          true,
	"to":            true,
	"fromTimestamp": true,
	"toTimestamp":   true,
}

func rawSQLBody(c *commonFlags) (map[string]any, error) {
	body, err := c.body()
	if err != nil {
		return nil, err
	}
	filtered := make(map[string]any, len(body))
	for key, value := range body {
		if rawSQLAllowedBodyFields[key] {
			filtered[key] = value
		}
	}
	return filtered, nil
}

func requireEventQueryMode(ctx context.Context, cl *query.Client, mode string, body map[string]any) error {
	capabilities, err := readEventCapabilities(ctx, cl, "query log query", body)
	if err != nil {
		return err
	}
	if !capabilities.Query {
		return fmt.Errorf("query log query: events queries are not supported by the current backend or API key")
	}
	if supported, declared := capabilities.Modes[mode]; !declared || !supported {
		return fmt.Errorf("query log query: mode %q is not supported by the current backend or API key", mode)
	}
	return nil
}

func requireEventList(ctx context.Context, cl *query.Client, operation string, body map[string]any) error {
	capabilities, err := readEventCapabilities(ctx, cl, operation, body)
	if err != nil {
		return err
	}
	if !capabilities.List {
		return fmt.Errorf("%s: event listing is not supported by the current backend or API key", operation)
	}
	return nil
}

type eventCapabilities struct {
	List  bool            `json:"list"`
	Query bool            `json:"query"`
	Modes map[string]bool `json:"modes"`
}

func readEventCapabilities(ctx context.Context, cl *query.Client, operation string, body map[string]any) (eventCapabilities, error) {
	params := url.Values{}
	if agent, ok := body["agent_uuid"]; ok {
		params.Set("agent_uuid", fmt.Sprint(agent))
	}
	metadata, err := cl.Get(ctx, cl.AgentURL("metadata"), params)
	if err != nil {
		return eventCapabilities{}, fmt.Errorf("%s: read backend capabilities: %w", operation, err)
	}
	var response struct {
		Capabilities struct {
			Events eventCapabilities `json:"events"`
		} `json:"capabilities"`
	}
	if err := json.Unmarshal(metadata, &response); err != nil {
		return eventCapabilities{}, fmt.Errorf("%s: parse backend capabilities: %w", operation, err)
	}
	return response.Capabilities.Events, nil
}

// queryLogExport lists events and writes them as JSONL into <dir>.
func queryLogExport(args []string) error {
	fs := flag.NewFlagSet("query log export", flag.ContinueOnError)
	c := registerCommon(fs)
	leading, rest := splitLeadingPos(args)
	if err := fs.Parse(rest); err != nil {
		return err
	}
	pos := append(leading, fs.Args()...)
	if len(pos) < 1 {
		return fmt.Errorf("usage: aiv query log export <dir> [--limit N]")
	}
	dir := pos[0]
	cl, err := c.client()
	if err != nil {
		return err
	}
	body, err := c.body()
	if err != nil {
		return err
	}
	if err := requireEventList(context.Background(), cl, "query log export", body); err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	data, err := cl.Post(context.Background(), cl.AgentURL("events/list"), body)
	if err != nil {
		return err
	}
	rows := query.ExtractRows(data)
	out := filepath.Join(dir, "events.jsonl")
	f, err := os.Create(out)
	if err != nil {
		return err
	}
	defer f.Close()
	for _, r := range rows {
		if _, err := f.Write(append(compactJSON(json.RawMessage(mustMarshal(r))), '\n')); err != nil {
			return err
		}
	}
	fmt.Printf("exported %d event(s) to %s\n", len(rows), out)
	return nil
}

// --- metric ------------------------------------------------------------------

func queryMetric(args []string) error {
	sub, rest := splitSub(args)
	switch sub {
	case "query":
		return queryMetricQuery(rest)
	case "help", "-h", "--help":
		fmt.Print(queryUsage)
		return nil
	default:
		return fmt.Errorf("query metric: unknown sub-command %q (want query)", sub)
	}
}

// queryMetricQuery posts the grafana time-series endpoint (rawSql mode).
func queryMetricQuery(args []string) error {
	fs := flag.NewFlagSet("query metric query", flag.ContinueOnError)
	c := registerCommon(fs)
	sql := fs.String("sql", "", "raw SQL time-series query (required)")
	refID := fs.String("ref-id", "A", "reference id for the query")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*sql) == "" {
		return fmt.Errorf("query metric query: --sql is required")
	}
	cl, err := c.client()
	if err != nil {
		return err
	}
	start, end, err := c.timeRange()
	if err != nil {
		return err
	}
	body := map[string]any{
		"mode":   "rawSql",
		"rawSql": *sql,
		"refId":  *refID,
		"from":   strconv.FormatInt(start, 10),
		"to":     strconv.FormatInt(end, 10),
	}
	if err := applyParams(body, c.params); err != nil {
		return err
	}
	data, err := cl.Post(context.Background(), cl.GrafanaURL(), body)
	if err != nil {
		return err
	}
	return c.emit(data)
}

// --- metadata ----------------------------------------------------------------

func queryMetadata(args []string) error {
	fs := flag.NewFlagSet("query metadata", flag.ContinueOnError)
	c := registerCommon(fs)
	c.noTime = true
	leading, rest := splitLeadingPos(args)
	if err := fs.Parse(rest); err != nil {
		return err
	}
	pos := append(leading, fs.Args()...)
	kind := ""
	if len(pos) > 0 {
		kind = pos[0]
	}
	var suffix string
	switch kind {
	case "":
		suffix = "metadata"
	case "events":
		suffix = "metadata/events"
	case "traces":
		suffix = "metadata/traces"
	case "help", "-h", "--help":
		fmt.Print(queryUsage)
		return nil
	default:
		return fmt.Errorf("query metadata: unknown kind %q (want events|traces)", kind)
	}
	cl, err := c.client()
	if err != nil {
		return err
	}
	params, err := c.query()
	if err != nil {
		return err
	}
	data, err := cl.Get(context.Background(), cl.AgentURL(suffix), params)
	if err != nil {
		return err
	}
	return c.emit(data)
}

// --- generic list ------------------------------------------------------------

// runList posts a body to an agent-scoped list endpoint and renders the result.
func runList(args []string, suffix string, eventCapability ...string) error {
	fs := flag.NewFlagSet("query "+suffix, flag.ContinueOnError)
	c := registerCommon(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	cl, err := c.client()
	if err != nil {
		return err
	}
	body, err := c.body()
	if err != nil {
		return err
	}
	if len(eventCapability) > 0 && eventCapability[0] == "events.list" {
		if err := requireEventList(context.Background(), cl, "query log list", body); err != nil {
			return err
		}
	}
	data, err := cl.Post(context.Background(), cl.AgentURL(suffix), body)
	if err != nil {
		return err
	}
	return c.emit(data)
}

// --- rendering ---------------------------------------------------------------

// emit writes the data payload as JSON or a table, to stdout or --o FILE.
func (c *commonFlags) emit(data json.RawMessage) error {
	out := os.Stdout
	if c.outFile != "" {
		f, err := os.Create(c.outFile)
		if err != nil {
			return err
		}
		defer f.Close()
		out = f
	}
	if strings.EqualFold(c.format, "json") {
		return writeJSON(out, data)
	}
	rows := query.ExtractRows(data)
	if rows == nil {
		// Not a collection: pretty-print the object so nothing is hidden.
		return writeJSON(out, data)
	}
	printRowTable(out, rows, splitCSV(c.fields))
	return nil
}

func printRowTable(out *os.File, rows []query.Row, preferred []string) {
	if len(rows) == 0 {
		fmt.Fprintln(out, "(no rows)")
		return
	}
	cols := query.CollectColumns(rows)
	if len(preferred) > 0 {
		// Preferred columns first (when present), then any extras.
		ordered := make([]string, 0, len(cols))
		seen := map[string]bool{}
		for _, p := range preferred {
			if inList(cols, p) && !seen[p] {
				ordered = append(ordered, p)
				seen[p] = true
			}
		}
		for _, col := range cols {
			if !seen[col] {
				ordered = append(ordered, col)
			}
		}
		cols = ordered
	}
	w := tabwriter.NewWriter(out, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, strings.Join(cols, "\t"))
	for _, r := range rows {
		cells := make([]string, len(cols))
		for i, col := range cols {
			cells[i] = formatCell(col, r[col])
		}
		fmt.Fprintln(w, strings.Join(cells, "\t"))
	}
	_ = w.Flush()
	fmt.Fprintf(out, "(%d row%s)\n", len(rows), plural(len(rows)))
}

func formatCell(field string, v any) string {
	if v == nil {
		return ""
	}
	if field == "timestamp" || strings.HasSuffix(field, "_time") || field == "startTime" {
		if s, ok := formatTimestamp(v); ok {
			return s
		}
	}
	s := stringify(v)
	if len(s) > 48 {
		s = s[:47] + "…"
	}
	return s
}

func formatTimestamp(v any) (string, bool) {
	switch t := v.(type) {
	case float64:
		return epochToStr(int64(t)), true
	case json.Number:
		if n, err := t.Int64(); err == nil {
			return epochToStr(n), true
		}
	case string:
		if strings.Contains(t, "T") {
			return t, true
		}
		if n, err := strconv.ParseInt(t, 10, 64); err == nil {
			return epochToStr(n), true
		}
	}
	return "", false
}

// epochToStr normalises s/ms/us/ns epoch values to RFC3339.
func epochToStr(n int64) string {
	switch {
	case n > 1e18:
		return time.Unix(0, n).UTC().Format(time.RFC3339)
	case n > 1e15:
		return time.UnixMicro(n).UTC().Format(time.RFC3339)
	case n > 1e12:
		return time.UnixMilli(n).UTC().Format(time.RFC3339)
	default:
		return time.Unix(n, 0).UTC().Format(time.RFC3339)
	}
}

func stringify(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64)
	case bool:
		return strconv.FormatBool(t)
	case nil:
		return ""
	default:
		b, err := json.Marshal(t)
		if err != nil {
			return fmt.Sprintf("%v", t)
		}
		return string(b)
	}
}

// --- small helpers -----------------------------------------------------------

func splitSub(args []string) (string, []string) {
	if len(args) == 0 {
		return "", nil
	}
	return args[0], args[1:]
}

// splitLeadingPos pulls off a leading positional argument (one not starting with
// '-') so both `get <id> --format json` and `get --format json <id>` work.
func splitLeadingPos(args []string) (leading, rest []string) {
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		return args[:1], args[1:]
	}
	return nil, args
}

func splitCSV(s string) []string {
	var out []string
	for _, f := range strings.Split(s, ",") {
		if f = strings.TrimSpace(f); f != "" {
			out = append(out, f)
		}
	}
	return out
}

// parseValue returns s decoded as JSON when possible, else the raw string.
func parseValue(s string) any {
	var v any
	if err := json.Unmarshal([]byte(s), &v); err == nil {
		return v
	}
	return s
}

// applyParams merges --param k=v entries into a body map (values JSON-decoded
// when possible).
func applyParams(body map[string]any, params []string) error {
	for _, kv := range params {
		i := strings.IndexByte(kv, '=')
		if i <= 0 {
			return fmt.Errorf("invalid --param %q (want k=v)", kv)
		}
		body[kv[:i]] = parseValue(kv[i+1:])
	}
	return nil
}

func rowID(r query.Row, keys ...string) string {
	for _, k := range keys {
		if v, ok := r[k]; ok {
			if s := stringify(v); s != "" {
				return s
			}
		}
	}
	return ""
}

// safeJoin resolves name under dir, rejecting anything that escapes dir. The
// substituted {trace_id} is server data, so contain it defensively.
func safeJoin(dir, name string) (string, error) {
	rooted := filepath.Clean(string(filepath.Separator) + name)
	full := filepath.Join(dir, rooted)
	rel, err := filepath.Rel(dir, full)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("refusing to write outside %s: %q", dir, name)
	}
	return full, nil
}

func mustMarshal(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		return []byte("{}")
	}
	return b
}

func compactJSON(raw json.RawMessage) []byte {
	if len(raw) == 0 {
		return []byte("{}")
	}
	var buf bytes.Buffer
	if err := json.Compact(&buf, raw); err != nil {
		return raw
	}
	return buf.Bytes()
}

func writeJSON(out *os.File, data json.RawMessage) error {
	var v any
	if len(data) == 0 {
		v = nil
	} else if err := json.Unmarshal(data, &v); err != nil {
		_, err := out.Write(data)
		return err
	}
	enc := json.NewEncoder(out)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}

func writeJSONFile(path string, data json.RawMessage) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return writeJSON(f, data)
}

func inList(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

func plural(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}

const queryUsage = `Query telemetry through the AI Vision OpenAPI using a space-scoped SpaceKey.

Usage:
  aiv query trace list   [--since 1h] [--limit 20] [--filter EXPR] [--fields a,b] [--format json]
  aiv query trace get <trace-id> [--format json]
  aiv query trace export <dir> [--limit N] [--filename-pattern '{trace_id}.json']
  aiv query trace metrics [--since 24h] [--bucket minute|hour|day] [--group-by model|provider]
  aiv query span list <trace-id> [<span-id>...]
  aiv query span get <trace-id> <span-id>
  aiv query session list [--limit 20]
  aiv query session get <session-id>
  aiv query session io <session-id>
  aiv query session usage <session-id>...
  aiv query log list   [--since 30m] [--filter "severity_text = 'ERROR'"]
  aiv query log query  [--mode fieldsJson|rawSql] [--sql "…"]
  aiv query log export <dir> [--limit N]
  aiv query metric query --sql "SELECT …" [--ref-id A]
  aiv query metadata [events|traces]

Commands:
  trace     List, inspect, export traces, or aggregate a complete time window.
  span      List spans of a trace or fetch one span.
  session   List sessions or inspect details, I/O and token usage.
  log       List events, run a fieldsJson/rawSql query, or export events.
  metric    Run a SQL timeseries query.
  metadata  Inspect the current space/Agent's available fields and metadata.

Query context:
  The selected profile supplies the platform URL and current space. That space's
  saved SpaceKey and most recently connected Agent product are used automatically.
  Run aiv key create/import to save a key and aiv connect <agent> to save an Agent.
  An account login alone does not authorize queries.

  --profile NAME selects a saved profile; otherwise $AIVISION_PROFILE or the
  active profile in ~/.aivision/config.json is used. Explicit connection flags
  override saved values. --space selects that space's saved key and Agent context;
  changing --endpoint requires credentials for the new endpoint.

  With no saved profile, AIVISION_ENDPOINT, AIVISION_SPACE_ID, AIVISION_SPACE_KEY
  and AIVISION_AGENT_PRODUCT supply defaults. Saved profiles take precedence over
  these environment values so stale shell exports cannot switch query credentials.

Common flags: --format table|json, -o FILE, --limit/-n, --page, --since/--until,
--last-n-minutes, --order-by, --fields, --filter, --param k=v (repeatable
passthrough), --profile, --space, --agent-product, --endpoint, --space-key.
Use aiv query <signal> <action> --help for that operation's flags.
Trace metrics uses JSON filters, optional --agent-uuid, and JSON output; it has no
list pagination. Scans above 10000 traces fail; results require meta.complete=true.

Time (--since/--until): RFC3339, epoch ms/s, "now", or a relative duration
(90s, 15m, 6h, 2d, 1w; an "ago" suffix is allowed). Defaults: since=1h ago,
until=now.

Examples:
  aiv query trace list --since 1h --limit 5
  aiv query trace get TRACE_ID --format json
  aiv query span list TRACE_ID
  aiv query session list --profile work
  aiv query trace list --agent-product Codex --format json
`
