package runtime

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"

	"github.com/antgroup/ai-vision/aivision-cli/internal/deploy/components"
)

// Status prints a per-component health table without mutating anything.
func (a *App) Status() error {
	a.Log.Step("status: namespace %s", a.BP.Metadata.Namespace)
	meta, _ := a.State.LoadMeta()
	if meta != nil && meta.DeployedAt != "" {
		a.Log.Info("last deploy: %s", meta.DeployedAt)
	}
	a.printOverrides()
	rows := [][]string{{"COMPONENT", "KIND", "STATE", "HEALTH", "ENDPOINT"}}
	// Components disabled by the effective vars (e.g. oceanbase when
	// OB_MANAGED=false) are not in OrderedComponents; surface the external
	// datastore explicitly so nobody wonders where it went.
	if !a.OBManaged() {
		rows = append(rows, []string{"database", "external", "-", "-",
			fmt.Sprintf("%s:%s user=%s", a.Vars["OB_HOST"], a.Vars["OB_HOST_PORT"], a.Vars["OB_USER"])})
	}
	widths := []int{9, 9, 5, 6, 8}
	ctx, cancel := context.WithTimeout(a.Ctx, 30*time.Second)
	defer cancel()
	for _, c := range a.BP.OrderedComponents() {
		vars := a.ComponentVars(c)
		state := "-"
		health := "-"
		endpoint := "-"
		switch c.Kind {
		case "container":
			name := a.ContainerName(c)
			if st := a.Docker.ContainerState(ctx, name); st != "" {
				state = st
			} else {
				state = "absent"
			}
		case "process":
			pid := a.State.ReadPid(c.Name)
			if pid > 0 && PidAlive(pid) {
				state = "running"
			} else if pid > 0 {
				state = "dead"
			} else {
				state = "stopped"
			}
		}
		if c.Health != nil {
			h := a.componentHealth(c, vars)
			oneShot := &Health{TCP: h.TCP, HTTP: h.HTTP, Exec: h.Exec, ExecArgv: h.ExecArgv, ExpectStatus: h.ExpectStatus, Timeout: h.Timeout}
			if ok, detail := a.Log.Probe(ctx, oneShot, parseDur(h.Timeout, 3*time.Second)); ok {
				health = "ok"
			} else {
				health = "down"
				_ = detail
			}
			if h.TCP != "" {
				endpoint = h.TCP
			} else if h.HTTP != "" {
				endpoint = h.HTTP
			}
		}
		rows = append(rows, []string{components.Canonical(c.Name), c.Kind, state, health, endpoint})
	}
	for _, row := range rows {
		for i, cell := range row {
			if len(cell) > widths[i] {
				widths[i] = len(cell)
			}
		}
	}
	for _, row := range rows {
		line := ""
		for i, cell := range row {
			line += pad(cell, widths[i]) + "  "
		}
		if row[0] == "COMPONENT" {
			a.Log.Raw("%s", strings.TrimRight(line, " "))
			a.Log.Raw("%s", strings.Repeat("-", len(strings.TrimRight(line, " "))))
			continue
		}
		// colorize health column
		a.Log.Raw("%s", strings.TrimRight(line, " "))
	}
	return nil
}

func pad(s string, w int) string {
	if len(s) >= w {
		return s
	}
	return s + strings.Repeat(" ", w-len(s))
}

// Logs tails a component's logs. follow streams until interrupted.
func (a *App) Logs(component string, lines int, follow bool) error {
	c := a.BP.Component(component)
	if c == nil {
		c = a.BP.Component(components.Legacy(component))
	}
	if c == nil {
		return fmt.Errorf("unknown component %q", component)
	}
	if lines <= 0 {
		lines = 50
	}
	switch c.Kind {
	case "container":
		name := a.ContainerName(*c)
		args := []string{"logs", "--tail", strconv.Itoa(lines)}
		if follow {
			args = append(args, "-f")
		}
		args = append(args, name)
		cmd := exec.CommandContext(a.Ctx, a.Docker.Bin, args...)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		return cmd.Run()
	case "process":
		logFile := expand(c.LogFile, a.ComponentVars(*c))
		if logFile == "" {
			logFile = a.State.LogFile(c.Name)
		} else if !isAbs(logFile) {
			logFile = a.State.LogDir + "/" + logFile
		}
		if _, err := os.Stat(logFile); err != nil {
			return fmt.Errorf("no log file at %s", logFile)
		}
		args := []string{"-n", strconv.Itoa(lines)}
		if follow {
			args = append(args, "-f")
		}
		args = append(args, logFile)
		cmd := exec.CommandContext(a.Ctx, "tail", args...)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		return cmd.Run()
	}
	return nil
}

func isAbs(p string) bool { return strings.HasPrefix(p, "/") }
