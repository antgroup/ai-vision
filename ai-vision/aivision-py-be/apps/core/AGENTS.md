# Django Core App

**Purpose:** Core business logic for AI Vision platform

## MODELS
| Model | Purpose | Key Fields |
|-------|---------|------------|
| User | Platform users | `uuid`, `normalized_email`, `name`, `status` |
| Space | Workspaces | `name`, `type`, `scope`, `creator` |
| SpaceMember | Space membership | `space`, `user`, `role` |
| ApiKey | API authentication | `key_hash`, `key_prefix`, `space` |
| Agent | Registered agents | `agent_product`, `name`, `space` |
| AgentTypeConfig | Agent type definitions | `agent_name`, `agent_product`, `setup_mode`, `config_guide`, `category` |
| DataSource | Data source configs | `agent_type`, `datasource_type`, `source_system` |
| Session | Agent sessions | `agent`, `trace_id`, `tokens_input/output` |

## ENUM CHOICES
| Field | Values |
|-------|--------|
| Space.type | 1=Personal, 2=Business |
| Space.scope | 1=Private, 2=Public (default: Private) |
| AgentTypeConfig.setup_mode | AUTO=Automatic discovery, APIKEY=APIKey integration, LANGFUSE=Langfuse integration, OTHER=Other methods (default: AUTO) |
| Status | 0=Disabled, 1=Normal |

## FIELD CONVENTIONS
- **Status**: `SmallIntegerField` with choices: `(0, "Disabled")`, `(1, "Normal")`
- **Timestamps**: common fields are `gmt_create`, `gmt_modified`, and `gmt_deleted`; check each model in `models/` rather than assuming every model has them.
- **ForeignKeys**: ALWAYS use `db_constraint=False` on ForeignKey and ManyToMany fields
- **Table names**: `aivision_` prefix via `db_table` Meta option

## AGENT CATALOG

`seed_data` initializes the local Claude Code, Codex, and custom Agent types.
The deployment's `configure_third_party_agents` step reads the supported
installable catalog. Earlier catalog entries remain available to deployments
that already have those records.

## API RESPONSE PATTERN
```python
# Success
{"code": 0, "message": "success", "data": {...}}

# Error
{"code": 1001, "message": "Error description"}
```

## NOTES
- Custom User model (not Django's auth.User)
- Models supporting soft deletion use `gmt_deleted`; preserve their query filters.
- `ApiKey.generate_key()` for secure key generation
