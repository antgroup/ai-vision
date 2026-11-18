package generate

import (
	"fmt"
	"github.com/antgroup/ai-vision/aivision-cli/internal/distribution"
	"io/fs"
	"regexp"
	"sort"
	"strings"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/assets"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/runtime"
	"gopkg.in/yaml.v3"
)

type object = map[string]any

// Product Compose is derived from the reviewed image blueprint. The renderer
// adds explicit lifecycle services; it never guesses how to translate arbitrary
// host shell hooks. Source/process blueprints keep the generic renderer.
func renderProductCompose(app *runtime.App, opts Options) ([]File, []string, error) {
	backend, managed := app.Vars["OB_BACKEND"], app.OBManaged()
	nativeRuntime := app.NativePythonRuntime()
	if distribution.NativeOnly && !nativeRuntime {
		return nil, nil, fmt.Errorf("native distribution requires NATIVE_PYTHON_RUNTIME=true")
	}
	vars := map[string]string{}
	for key, value := range app.Vars {
		vars[key] = value
	}
	if opts.Parameterized {
		vars = productTemplateVars(app)
	}
	expand := func(value string) string {
		return productExpand(value, vars)
	}
	expandSlice := func(values []string) []string {
		result := make([]string, len(values))
		for i, value := range values {
			result[i] = expand(value)
		}
		return result
	}
	scriptRoot := opts.BootstrapPath
	if scriptRoot == "" {
		scriptRoot = "./bootstrap"
	}
	scriptMount := scriptRoot + ":/opt/aivision/bootstrap:ro"
	databaseScriptMount := scriptMount
	if nativeRuntime {
		// Parameterized templates may point into a checkout containing legacy
		// Python helpers. Mount only the datastore's shell lifecycle asset.
		databaseScriptMount = scriptRoot + "/" + backend + ".sh:/opt/aivision/bootstrap/" + backend + ".sh:ro"
	}
	services, volumes := object{}, object{}
	dep := func(condition string, names ...string) object {
		result := object{}
		for _, name := range names {
			result[name] = object{"condition": condition}
		}
		return result
	}
	health := func(argv ...string) object {
		return object{"test": append([]string{"CMD"}, argv...), "interval": "10s", "timeout": "8s", "retries": 3, "start_period": "60s"}
	}
	httpHealth := func(port, path string) object {
		if nativeRuntime {
			return health("/usr/local/bin/aiv-http-probe", "127.0.0.1", expand(port), path, "3")
		}
		endpoint := expand("http://127.0.0.1:" + port + path)
		return health(sourceHTTPHealth(endpoint)...)
	}
	for _, c := range app.BP.OrderedComponents() {
		if c.Kind != "container" {
			return nil, nil, fmt.Errorf("product Compose requires container components; use the source blueprint with aiv cast")
		}
		id := components.Canonical(c.Name)
		service := object{
			"image": expand(c.Image), "container_name": expand(c.ContainerName),
			"restart":           "unless-stopped",
			"stop_grace_period": "60s",
			"networks":          object{"stack": object{"aliases": []string{id, c.Name}}},
			"labels":            object{"io.aivision.component": id, "io.aivision.product": "aivision"},
			"pull_policy":       expand("${IMAGE_PULL}"),
		}
		if c.MemoryLimit != "" {
			service["mem_limit"] = expand(c.MemoryLimit)
		}
		env := map[string]string{}
		for key, value := range c.Env {
			env[key] = expand(value)
		}
		service["environment"] = env
		if len(c.Ports) > 0 {
			service["ports"] = expandSlice(c.Ports)
		}
		if len(c.Entrypoint) > 0 {
			service["entrypoint"] = expandSlice(c.Entrypoint)
		}
		if len(c.Command) > 0 {
			service["command"] = expandSlice(c.Command)
		}
		mounts := []string{}
		for _, volume := range c.Volumes {
			parts := strings.SplitN(volume, ":", 2)
			name := expand(parts[0])
			// Compose resource keys must be literal; physical volume names retain
			// the existing namespace association through the explicit name field.
			key := fmt.Sprintf("%s-data-%d", id, len(mounts))
			volumes[key] = object{"name": name}
			mounts = append(mounts, key+":"+expand(parts[1]))
		}
		for _, mount := range c.Mounts {
			ms := parseMount(expand(mount))
			spec := ms.Source + ":" + ms.Target
			if ms.ReadOnly {
				spec += ":ro"
			}
			mounts = append(mounts, spec)
		}
		switch id {
		case "database":
			service["stop_grace_period"] = "120s"
			if backend == "seekdb" {
				seekEnv := map[string]string{
					"AIV_SEEKDB_PASSWORD": vars["OB_TENANT_PASSWORD"], "AIV_SEEKDB_MEMORY_BUDGET": vars["SEEKDB_MEMORY_BUDGET"],
					"AIV_SEEKDB_CPU": vars["SEEKDB_CPU_COUNT"], "AIV_SEEKDB_DATA": vars["SEEKDB_DATAFILE_SIZE"],
					"AIV_SEEKDB_LOG": vars["SEEKDB_LOG_DISK_SIZE"],
				}
				mounts = append(mounts, databaseScriptMount)
				service["environment"] = seekEnv
				service["entrypoint"] = []string{"/bin/sh", "/opt/aivision/bootstrap/seekdb.sh"}
				service["command"] = []string{"serve"}
				service["ulimits"] = object{"nofile": object{"soft": 65536, "hard": 65536}}
				service["healthcheck"] = health("/bin/sh", "/opt/aivision/bootstrap/seekdb.sh", "health")
				services["database-initialize"] = object{
					"image": expand(c.Image), "entrypoint": service["entrypoint"], "command": []string{"initialize"},
					"environment": seekEnv, "volumes": mounts, "networks": []string{"initialization"},
					"profiles": []string{"bootstrap"}, "restart": "no", "ulimits": service["ulimits"], "mem_limit": service["mem_limit"],
					"stop_grace_period": "120s",
				}
			} else {
				mounts = append(mounts, databaseScriptMount)
				service["entrypoint"] = []string{"/bin/sh", "/opt/aivision/bootstrap/oceanbase.sh"}
				service["healthcheck"] = object{
					"test":     []string{"CMD-SHELL", `MYSQL_PWD="$$OB_TENANT_PASSWORD" timeout --signal=TERM --kill-after=1s 3s obclient -h127.0.0.1 -P2881 -u"root@$$OB_TENANT_NAME" -e 'SELECT 1' >/dev/null 2>&1`},
					"interval": "10s", "timeout": "8s", "retries": 3, "start_period": "600s",
				}
			}
			service["healthcheck"].(object)["start_period"] = "600s"
		case "data-backend":
			service["depends_on"] = dep("service_completed_successfully", "database-init")
			// Keep checking the native endpoint after the initial readiness Job.
			// The supported image needs neither a shell nor curl for this probe.
			service["healthcheck"] = health("/app/signoz", "healthcheck", "--ready")
		case "ingest-gateway":
			service["depends_on"] = dep("service_completed_successfully", "data-backend-ready")
			service["healthcheck"] = health("/app/collector", "healthcheck", "--ready")
			env["GOMEMLIMIT"] = "576MiB"
		case "backend":
			service["depends_on"] = dep("service_completed_successfully", "database-init", "data-backend-ready")
			service["healthcheck"] = httpHealth("${BE_PORT}", "/api/v1/ready")
			service["healthcheck"].(object)["start_period"] = "600s"
		case "diagnosis":
			service["stop_grace_period"] = "300s"
			delete(env, "SIGNOZ_ORG_ID")
			env["SIGNOZ_ORG_ID_FILE"] = "/run/aivision/org-id"
			mounts = append(mounts, "runtime:/run/aivision:ro")
			service["depends_on"] = dep("service_completed_successfully", "backend-bootstrap")
			service["depends_on"].(object)["ingest-gateway"] = object{"condition": "service_healthy"}
			service["healthcheck"] = httpHealth("${AGENTLENS_PORT}", "/internal/readyz")
			service["healthcheck"].(object)["start_period"] = "180s"
		case "frontend":
			service["stop_signal"], service["stop_grace_period"] = "SIGQUIT", "30s"
			service["depends_on"] = dep("service_healthy", "backend")
			service["depends_on"].(object)["backend-bootstrap"] = object{"condition": "service_completed_successfully"}
			// nginx:alpine-slim ships curl; it does not include wget.
			service["healthcheck"] = health("curl", "-fsS", "-o", "/dev/null", expand("http://127.0.0.1:${FE_PORT}/"))
			service["healthcheck"].(object)["start_period"] = "30s"
		default:
			return nil, nil, fmt.Errorf("unsupported product component %q", c.Name)
		}
		if len(mounts) > 0 {
			service["volumes"] = mounts
		}
		services[id] = service
	}
	databaseInit := object{
		"image": expand("${AGENTLENS_IMAGE}"), "entrypoint": productBootstrapEntrypoint("database-init", nativeRuntime),
		"restart": "no", "networks": []string{"stack"}, "volumes": []string{scriptMount},
		"environment": object{"OB_HOST": expand("${OB_HOST}"), "OB_HOST_PORT": vars["OB_HOST_PORT"],
			"OB_USER": expand("${OB_USER}"), "OB_TENANT_PASSWORD": vars["OB_TENANT_PASSWORD"], "OB_ATTEMPTS": "200"},
	}
	if managed {
		databaseInit["depends_on"] = dep("service_healthy", "database")
	}
	services["database-init"] = databaseInit
	services["data-backend-ready"] = object{
		"image": expand("${BE_IMAGE}"), "entrypoint": productBootstrapEntrypoint("wait-http", nativeRuntime),
		"command": []string{expand("http://${SIGNOZ_CONTAINER}:8080/api/v1/ready")},
		"restart": "no", "networks": []string{"stack"}, "volumes": []string{scriptMount},
		"depends_on": dep("service_started", "data-backend"),
	}
	be, ok := services["backend"].(object)
	if !ok {
		return nil, nil, fmt.Errorf("product Compose requires the backend component")
	}
	services["backend-bootstrap"] = object{
		"image": be["image"], "entrypoint": productBootstrapEntrypoint("backend-bootstrap", nativeRuntime),
		"environment": be["environment"], "restart": "no", "networks": []string{"stack"},
		"volumes":    []string{scriptMount, "runtime:/run/aivision"},
		"depends_on": dep("service_healthy", "backend"),
	}
	if nativeRuntime {
		delete(databaseInit, "volumes")
		delete(services["data-backend-ready"].(object), "volumes")
		services["backend-bootstrap"].(object)["volumes"] = []string{"runtime:/run/aivision"}
	}
	volumes["runtime"] = object{"name": expand("aivforge-runtime-${NAMESPACE}")}
	for _, raw := range volumes {
		raw.(object)["labels"] = object{
			"io.aivision.product":   "aivision",
			"io.aivision.namespace": vars["NAMESPACE"],
			"io.aivision.state-dir": vars["STATE_DIR"],
		}
	}
	networks := object{"stack": object{"name": expand("${DOCKER_NETWORK}")}}
	if managed && backend == "seekdb" {
		networks["initialization"] = object{"internal": true}
	}
	document := object{
		"name": vars["NAMESPACE"], "services": services, "networks": networks, "volumes": volumes,
		"x-aivision": object{"backend": backend, "managed": managed, "namespace": vars["NAMESPACE"],
			"state_dir": vars["STATE_DIR"], "history_start": vars["AIVISION_NATIVE_TRACE_HISTORY_START"]},
	}
	aliases := map[string]string{}
	for _, component := range components.All() {
		for _, alias := range append(append([]string{component.ID, component.LegacyName, component.DockerName}, component.Aliases...), component.Kubernetes.Name) {
			if alias != "" {
				aliases[alias] = component.ID
			}
		}
	}
	document["x-aivision"].(object)["component_aliases"] = aliases
	for name, raw := range services {
		service := raw.(object)
		if _, ok := service["labels"]; !ok {
			service["labels"] = object{"io.aivision.component": name, "io.aivision.product": "aivision"}
		}
	}
	if !opts.Parameterized {
		document = escapeComposeValues(document).(object)
	}
	data, err := yaml.Marshal(document)
	if err != nil {
		return nil, nil, err
	}
	files := []File{{Path: "compose.yaml", Content: append([]byte("# Generated from deploy/blueprints/aivision.yaml; run renderproduct to refresh.\n"), data...)}}
	names, err := fs.Glob(assets.Files, "bootstrap/*")
	if err != nil {
		return nil, nil, err
	}
	sort.Strings(names)
	for _, name := range names {
		if nativeRuntime && strings.HasSuffix(name, ".py") {
			continue
		}
		content, err := assets.Files.ReadFile(name)
		if err != nil {
			return nil, nil, err
		}
		files = append(files, File{Path: name, Content: content})
	}
	warning := "product Compose: use aiv cast --target compose --compose-file <compose.yaml> --compose-env-file <private-env>; aiv validates ownership and runs isolated initialization before native Compose up"
	if !opts.Parameterized {
		warning = "product Compose export contains resolved private configuration; use aiv cast --target compose --compose-file <compose.yaml> --compose-env-file /dev/null, and regenerate from the original CLI vars to change images or configuration"
	}
	return files, []string{warning}, nil
}

// Prefer native helpers included in compiled images. Existing source images use
// the same mounted bootstrap files; both paths preserve argv and bypass API boot.
func bootstrapEntrypoint(name string) []string {
	return []string{"/bin/sh", "-ec", fmt.Sprintf(
		`if [ -x /app/%s ]; then exec /app/%s "$@"; fi; exec python /opt/aivision/bootstrap/%s.py "$@"`,
		name, name, name), "aiv-bootstrap"}
}

func productBootstrapEntrypoint(name string, native bool) []string {
	if native {
		return []string{"/app/" + name}
	}
	return bootstrapEntrypoint(name)
}

func productTemplateVars(app *runtime.App) map[string]string {
	vars := map[string]string{}
	secrets := map[string]bool{"OB_TENANT_PASSWORD": true, "OB_SYS_PASSWORD": true, "SECRET_KEY": true,
		"SIGNOZ_INGEST_INTERNAL_KEY": true, "AIVISION_INGEST_SECRET": true, "AGENTLENS_TOKEN": true, "SIGNOZ_USER_ROOT_PASSWORD": true}
	for key, value := range app.BP.Config.Vars {
		if secrets[key] {
			vars[key] = "${" + key + ":?set " + key + " in the private env file}"
		} else {
			vars[key] = "${" + key + ":-" + value + "}"
		}
	}
	for key, component := range components.ServiceImageComponents() {
		if app.BP.Config.Vars[key] != component.DefaultImageTemplate() {
			continue
		}
		registry := app.BP.Config.Vars["IMAGE_REGISTRY"]
		image := vars["IMAGE_REGISTRY"] + "/" + component.ImageName(registry)
		if registry == components.ProductImageRegistry {
			// An unset prefix selects the official role image. Existing env
			// files that supply a mirror prefix keep their original basename.
			image = "${IMAGE_REGISTRY:-" + registry + "/" + component.ID + "}" +
				"${IMAGE_REGISTRY:+/" + component.DockerName + "}"
		}
		vars[key] = "${" + key + ":-" + image + ":" + vars["IMAGE_TAG"] + "}"
	}
	vars["NAMESPACE"] = "${NAMESPACE:-aivision}"
	vars["STATE_DIR"] = "${AIV_STATE_DIR:?set absolute AIV_STATE_DIR}"
	vars["LOG_DIR"] = "${AIV_LOG_DIR:?set absolute AIV_LOG_DIR}"
	vars["DOCKER_NETWORK"] = "${DOCKER_NETWORK:-aivision-net}"
	vars["OB_BACKEND"], vars["OB_MANAGED"] = app.Vars["OB_BACKEND"], app.Vars["OB_MANAGED"]
	for key, id := range map[string]string{"OB_CONTAINER": "database", "SIGNOZ_CONTAINER": "data-backend",
		"GATEWAY_CONTAINER": "ingest-gateway", "BE_CONTAINER": "backend", "AGENTLENS_CONTAINER": "diagnosis", "FE_CONTAINER": "frontend"} {
		vars[key] = "${" + key + ":-${NAMESPACE:-aivision}-" + id + "}"
	}
	vars["OB_IMAGE"] = "${OB_IMAGE:-" + app.Vars["OB_IMAGE"] + "}"
	vars["OB_USER"] = "${OB_USER:-root@${OB_TENANT:-aivision}}"
	vars["OB_USER_URL"] = "${OB_USER_URL:-root%40${OB_TENANT:-aivision}}"
	if app.Vars["OB_BACKEND"] == "seekdb" {
		vars["OB_USER"] = "${OB_USER:-root}"
		vars["OB_USER_URL"] = "${OB_USER_URL:-root}"
	}
	if !app.OBManaged() {
		vars["OB_HOST"] = "${OB_HOST:?set container-reachable OB_HOST}"
	}
	vars["SEEKDB_MEMORY_BUDGET"] = "${SEEKDB_MEMORY_BUDGET:-${SEEKDB_MEMORY_LIMIT:-2G}}"
	vars["SEEKDB_VOLUME"] = "aivforge-seekdb-data-${NAMESPACE:-aivision}"
	vars["AIVISION_NATIVE_TRACE_HISTORY_START"] = "${AIVISION_NATIVE_TRACE_HISTORY_START:?set fixed UTC retained-history bound}"
	for _, component := range components.All() {
		for role, legacy := range component.ConfigAliases {
			if value, ok := vars[legacy]; ok {
				vars[legacy] = "${" + role + ":-" + value + "}"
			}
		}
	}
	for i := 0; i < 8; i++ {
		for key, value := range vars {
			vars[key] = productExpand(value, vars)
		}
	}
	return vars
}

var productVariable = regexp.MustCompile(`\$\{([A-Za-z_][A-Za-z0-9_]*)\}`)

// Unlike os.Expand, this does not consume Compose ${KEY:-default} expressions.
func productExpand(value string, vars map[string]string) string {
	return productVariable.ReplaceAllStringFunc(value, func(token string) string {
		return vars[token[2:len(token)-1]]
	})
}

func escapeComposeValues(value any) any {
	escape := func(value string) string { return strings.ReplaceAll(value, "$", "$$") }
	switch node := value.(type) {
	case map[string]any:
		result := object{}
		for key, item := range node {
			result[key] = escapeComposeValues(item)
		}
		return result
	case map[string]string:
		result := map[string]string{}
		for key, item := range node {
			result[key] = escape(item)
		}
		return result
	case []string:
		if len(node) > 0 && node[0] == "CMD-SHELL" {
			return node // The one intentional container-shell expression.
		}
		result := make([]string, len(node))
		for i, item := range node {
			result[i] = escape(item)
		}
		return result
	case string:
		return escape(node)
	}
	return value
}
