# AI Vision Skills have moved

`ob-labs/ai-vision-skills` is the maintained source for AI Vision Skills, including
their bundled references, scripts, and tests. If access is restricted, authenticate
with a GitHub account that can read the repository. Product source, CLI builds,
deployment assets, images, and product releases are maintained in the
[official AI Vision repository](https://github.com/antgroup/ai-vision).

| Skill | Purpose |
|---|---|
| `aivision-quickstart` | Configure a model and try real requests, Trace/Session readback, and diagnosis on an existing platform |
| `aivision-deploy` | Deploy, verify, and upgrade AI Vision using released CLI assets |
| `aivision-trace` | Query, inspect, and export traces |
| `aivision-metric` | Query trace metrics and usage coverage |

## Install or migrate

For a new global installation:

```bash
npx skills add ob-labs/ai-vision-skills --skill aivision-quickstart --global
npx skills add ob-labs/ai-vision-skills --skill aivision-deploy --global
npx skills add ob-labs/ai-vision-skills --skill aivision-trace --global
npx skills add ob-labs/ai-vision-skills --skill aivision-metric --global
```

Install only the Skills you need. Omit `--global` for a project installation;
use the same scope and agent targets when migrating an existing installation.

If you installed the Skills from an earlier product repository, explicitly reinstall
them from `ob-labs/ai-vision-skills`. The `npx skills update` command uses the
recorded origin. Before reinstalling, inspect the installed copies and preserve
any local edits. Remove only the old-source copies of the four
named Skills that you are migrating, including their agent-specific links, then
run the corresponding commands above for the same scope and targets. Confirm
that each migrated Skill has one installed copy with the new origin and its
bundled resources. Preserve unrelated user Skills and their copies; do not
remove an entire agent skills directory or its shared store.

For manual installations, copy the complete selected Skill directory from the
new repository into the agent's skills directory, after preserving any edited
old copy. The Skills use released CLI assets. For direct CLI use, see the
[deployment guide](../docs/deployment/README.md), [query guide](../aivision-cli/docs/query.md),
or [source-build instructions](../aivision-cli/README.md#source-build-pointing-at-a-different-repo-or-branch).
