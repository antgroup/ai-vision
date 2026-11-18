"""
DataSource config 和 AgentDashboardPanel panel_config 的 JSON Schema 校验。

在 Django Admin 的 add/update 时自动触发，防止写入不合法的配置。
"""

from django.core.exceptions import ValidationError

VALID_RESPONSE_FORMATS = {"prometheus", "table", "langfuse"}
VALID_VALUE_TYPES = {"number", "timeseries", "table"}
VALID_QUERY_MODES = {"simple", "template"}
VALID_COLUMN_TYPES = {"string", "number", "datetime", "boolean"}
VALID_PANEL_TYPES = {"stat_card", "timeseries_chart", "table", "pie_chart", "bar_chart"}
VALID_EVENT_QUERY_MODES = {"fieldsJson", "rawSql"}


def validate_datasource_config(config: dict, datasource_type: str, source_system: str):
    """
    校验 DataSource.config 的结构完整性和合法性。

    Args:
        config: DataSource.config JSON 字段
        datasource_type: metrics / logs / traces
        source_system: signoz / langfuse
    """
    if not isinstance(config, dict):
        raise ValidationError("config 必须是一个 JSON 对象")

    # 校验顶层 response_format
    response_format = config.get("response_format")
    if not response_format:
        raise ValidationError("config 缺少必填字段: response_format")
    if response_format not in VALID_RESPONSE_FORMATS:
        raise ValidationError(
            f"response_format 值 '{response_format}' 不合法，"
            f"可选值: {', '.join(sorted(VALID_RESPONSE_FORMATS))}"
        )

    # 校验 queries（可选，V2 不再依赖此字段）
    queries = config.get("queries")
    if queries:
        if not isinstance(queries, dict):
            raise ValidationError(
                "config.queries 必须是一个 JSON 对象（key-value 形式）"
            )
        for data_item_key, query_def in queries.items():
            _validate_query_definition(
                data_item_key, query_def, datasource_type, source_system
            )

    _validate_openapi_events_config(config, datasource_type, source_system)


def _validate_query_definition(
    data_item_key: str,
    query_def: dict,
    datasource_type: str,
    source_system: str,
):
    """校验单个 query definition"""
    prefix = f"queries.{data_item_key}"

    if not isinstance(query_def, dict):
        raise ValidationError(f"{prefix} 必须是一个 JSON 对象")

    # 校验 mode
    mode = query_def.get("mode")
    if not mode:
        raise ValidationError(f"{prefix} 缺少必填字段: mode")
    if mode not in VALID_QUERY_MODES:
        raise ValidationError(
            f"{prefix}.mode 值 '{mode}' 不合法，可选值: {', '.join(sorted(VALID_QUERY_MODES))}"
        )

    # 校验 value_type
    value_type = query_def.get("value_type")
    if not value_type:
        raise ValidationError(f"{prefix} 缺少必填字段: value_type")
    if value_type not in VALID_VALUE_TYPES:
        raise ValidationError(
            f"{prefix}.value_type 值 '{value_type}' 不合法，"
            f"可选值: {', '.join(sorted(VALID_VALUE_TYPES))}"
        )

    # 根据 mode 做不同校验
    if mode == "template":
        _validate_template_mode(prefix, query_def)
    elif mode == "simple":
        _validate_simple_mode(prefix, query_def, datasource_type, source_system)

    # 校验 display（可选但如果存在需要有 label）
    display = query_def.get("display")
    if display is not None:
        if not isinstance(display, dict):
            raise ValidationError(f"{prefix}.display 必须是一个 JSON 对象")
        if not display.get("label"):
            raise ValidationError(f"{prefix}.display 缺少必填字段: label")

    # 校验 columns（可选，如果存在需要是列表）
    columns = query_def.get("columns")
    if columns is not None:
        _validate_columns(prefix, columns)


def _validate_template_mode(prefix: str, query_def: dict):
    """校验 template 模式的必填字段"""
    template = query_def.get("template")
    if not template:
        raise ValidationError(f"{prefix} 使用 template 模式但缺少 template 字段")
    if not isinstance(template, str):
        raise ValidationError(f"{prefix}.template 必须是字符串")
    if len(template.strip()) == 0:
        raise ValidationError(f"{prefix}.template 不能为空字符串")

    # 尝试解析 Jinja2 模板语法是否正确
    try:
        from jinja2.sandbox import SandboxedEnvironment

        env = SandboxedEnvironment()
        env.parse(template)
    except Exception as exc:
        raise ValidationError(f"{prefix}.template Jinja2 语法错误: {exc}") from exc


def _validate_simple_mode(
    prefix: str,
    query_def: dict,
    datasource_type: str,
    source_system: str,
):
    """校验 simple 模式的必填字段（根据 datasource_type 不同）"""

    if datasource_type == "metrics":
        _validate_simple_metrics(prefix, query_def)
    elif datasource_type == "traces" or source_system == "langfuse":
        _validate_simple_traces(prefix, query_def)


def _validate_simple_metrics(prefix: str, query_def: dict):
    """校验 metrics simple 模式"""
    metric = query_def.get("metric")
    if not metric:
        raise ValidationError(f"{prefix} 使用 simple 模式（metrics）但缺少 metric 字段")
    if not isinstance(metric, str):
        raise ValidationError(f"{prefix}.metric 必须是字符串")

    # filters 可选，但如果存在需要是 dict
    filters = query_def.get("filters")
    if filters is not None and not isinstance(filters, dict):
        raise ValidationError(f"{prefix}.filters 必须是一个 JSON 对象")

    # aggregations 可选，但如果存在需要是 list
    aggregations = query_def.get("aggregations")
    if aggregations is not None:
        if not isinstance(aggregations, list):
            raise ValidationError(f"{prefix}.aggregations 必须是一个数组")
        for index, aggregation in enumerate(aggregations):
            if not isinstance(aggregation, dict):
                raise ValidationError(
                    f"{prefix}.aggregations[{index}] 必须是一个 JSON 对象"
                )
            if not aggregation.get("func"):
                raise ValidationError(
                    f"{prefix}.aggregations[{index}] 缺少必填字段: func"
                )


def _validate_simple_logs(prefix: str, query_def: dict):
    """校验 logs simple 模式"""
    table = query_def.get("table")
    if not table:
        raise ValidationError(f"{prefix} 使用 simple 模式（logs）但缺少 table 字段")
    if not isinstance(table, str):
        raise ValidationError(f"{prefix}.table 必须是字符串")

    # filters 可选
    filters = query_def.get("filters")
    if filters is not None and not isinstance(filters, dict):
        raise ValidationError(f"{prefix}.filters 必须是一个 JSON 对象")

    # columns 可选
    columns = query_def.get("columns")
    if columns is not None:
        _validate_columns(prefix, columns)


def _validate_simple_traces(prefix: str, query_def: dict):
    """校验 traces simple 模式"""
    sdk_params = query_def.get("sdk_params")
    if not sdk_params:
        raise ValidationError(
            f"{prefix} 使用 simple 模式（traces）但缺少 sdk_params 字段"
        )
    if not isinstance(sdk_params, dict):
        raise ValidationError(f"{prefix}.sdk_params 必须是一个 JSON 对象")

    method = sdk_params.get("method")
    if not method:
        raise ValidationError(f"{prefix}.sdk_params 缺少必填字段: method")
    if not isinstance(method, str):
        raise ValidationError(f"{prefix}.sdk_params.method 必须是字符串")

    param_mapping = sdk_params.get("param_mapping")
    if param_mapping is not None and not isinstance(param_mapping, dict):
        raise ValidationError(f"{prefix}.sdk_params.param_mapping 必须是一个 JSON 对象")


def _validate_columns(prefix: str, columns: list):
    """校验 columns 数组"""
    if not isinstance(columns, list):
        raise ValidationError(f"{prefix}.columns 必须是一个数组")

    for index, col in enumerate(columns):
        col_prefix = f"{prefix}.columns[{index}]"

        if isinstance(col, str):
            continue

        if not isinstance(col, dict):
            raise ValidationError(f"{col_prefix} 必须是字符串或 JSON 对象")

        if not col.get("key"):
            raise ValidationError(f"{col_prefix} 缺少必填字段: key")
        if not col.get("label"):
            raise ValidationError(f"{col_prefix} 缺少必填字段: label")

        col_type = col.get("type")
        if col_type and col_type not in VALID_COLUMN_TYPES:
            raise ValidationError(
                f"{col_prefix}.type 值 '{col_type}' 不合法，"
                f"可选值: {', '.join(sorted(VALID_COLUMN_TYPES))}"
            )


def _validate_openapi_events_config(
    config: dict, datasource_type: str, source_system: str
):
    if datasource_type != "logs" or source_system != "signoz":
        return

    openapi = config.get("openapi")
    if openapi is None:
        return
    if not isinstance(openapi, dict):
        raise ValidationError("config.openapi 必须是一个 JSON 对象")

    events = openapi.get("events")
    if events is None:
        return
    if not isinstance(events, dict):
        raise ValidationError("config.openapi.events 必须是一个 JSON 对象")

    query_modes = events.get("query_modes")
    if query_modes is not None:
        _validate_string_list(
            "config.openapi.events.query_modes",
            query_modes,
            allowed=VALID_EVENT_QUERY_MODES,
        )

    default_fields = events.get("default_fields")
    if default_fields is not None:
        _validate_string_list("config.openapi.events.default_fields", default_fields)

    id_fields = events.get("id_fields")
    if id_fields is not None:
        _validate_string_list("config.openapi.events.id_fields", id_fields)

    field_mappings = events.get("field_mappings")
    if field_mappings is not None:
        if not isinstance(field_mappings, dict):
            raise ValidationError(
                "config.openapi.events.field_mappings 必须是一个 JSON 对象"
            )
        for public_key, candidates in field_mappings.items():
            if not isinstance(public_key, str) or not public_key:
                raise ValidationError(
                    "config.openapi.events.field_mappings 的 key 必须是非空字符串"
                )
            if isinstance(candidates, str):
                continue
            _validate_string_list(
                f"config.openapi.events.field_mappings.{public_key}",
                candidates,
            )

    limits = events.get("limits")
    if limits is not None:
        _validate_event_limits("config.openapi.events.limits", limits)

    grafana = events.get("grafana")
    if grafana is not None:
        if not isinstance(grafana, dict):
            raise ValidationError("config.openapi.events.grafana 必须是一个 JSON 对象")
        grafana_modes = grafana.get("query_modes")
        if grafana_modes is not None:
            _validate_string_list(
                "config.openapi.events.grafana.query_modes",
                grafana_modes,
                allowed=VALID_EVENT_QUERY_MODES,
            )
        required_macros = grafana.get("required_macros")
        if required_macros is not None:
            _validate_string_list(
                "config.openapi.events.grafana.required_macros",
                required_macros,
                allowed={
                    "table",
                    "time_filter",
                    "scope_filter",
                    "agent_product_filter",
                },
            )
        allowed_output_aliases = grafana.get("allowed_output_aliases")
        if allowed_output_aliases is not None:
            _validate_string_list(
                "config.openapi.events.grafana.allowed_output_aliases",
                allowed_output_aliases,
            )
        for key in ("max_rows", "timeout_ms", "max_sql_length"):
            if key in grafana:
                _validate_positive_int(
                    f"config.openapi.events.grafana.{key}", grafana[key]
                )


def _validate_string_list(prefix: str, value, allowed: set[str] | None = None):
    if not isinstance(value, list):
        raise ValidationError(f"{prefix} 必须是一个字符串数组")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise ValidationError(f"{prefix}[{index}] 必须是非空字符串")
        if allowed is not None and item not in allowed:
            raise ValidationError(
                f"{prefix}[{index}] 值 '{item}' 不合法，可选值: {', '.join(sorted(allowed))}"
            )


def _validate_positive_int(prefix: str, value):
    if not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{prefix} 必须是正整数")


def _validate_event_limits(prefix: str, value):
    if not isinstance(value, dict):
        raise ValidationError(f"{prefix} 必须是一个 JSON 对象")
    max_page = value.get("max_page_size")
    default_page = value.get("default_page_size")
    max_days = value.get("max_time_range_days")
    for key in ("max_page_size", "default_page_size", "max_time_range_days"):
        if key in value:
            _validate_positive_int(f"{prefix}.{key}", value[key])
    if isinstance(max_page, int) and max_page > 200:
        raise ValidationError(f"{prefix}.max_page_size 不能超过 200")
    if (
        isinstance(default_page, int)
        and isinstance(max_page, int)
        and default_page > max_page
    ):
        raise ValidationError(f"{prefix}.default_page_size 不能超过 max_page_size")
    if isinstance(max_days, int) and max_days > 90:
        raise ValidationError(f"{prefix}.max_time_range_days 不能超过 90")


def validate_panel_config(panel_config: dict, panel_type: str):
    """
    校验 AgentDashboardPanel.panel_config 的结构。

    panel_config 是可选的扩展配置，不同 panel_type 可能有不同的配置项。
    """
    if not isinstance(panel_config, dict):
        raise ValidationError("panel_config 必须是一个 JSON 对象")

    if panel_type == "stat_card":
        # stat_card 可选配置: unit, decimal_places, prefix, suffix, color
        unit = panel_config.get("unit")
        if unit is not None and not isinstance(unit, str):
            raise ValidationError("panel_config.unit 必须是字符串")

        decimal_places = panel_config.get("decimal_places")
        if decimal_places is not None and not isinstance(decimal_places, int):
            raise ValidationError("panel_config.decimal_places 必须是整数")

    elif panel_type == "timeseries_chart":
        # timeseries_chart 可选配置: y_axis_label, step
        y_axis_label = panel_config.get("y_axis_label")
        if y_axis_label is not None and not isinstance(y_axis_label, str):
            raise ValidationError("panel_config.y_axis_label 必须是字符串")

    elif panel_type == "table":
        # table 可选配置: page_size, sortable
        page_size = panel_config.get("page_size")
        if page_size is not None:
            if not isinstance(page_size, int) or page_size <= 0:
                raise ValidationError("panel_config.page_size 必须是正整数")

    elif panel_type in ("pie_chart", "bar_chart"):
        # 饼图/柱状图 可选配置: legend_position
        legend_position = panel_config.get("legend_position")
        valid_positions = {"top", "bottom", "left", "right"}
        if legend_position is not None and legend_position not in valid_positions:
            raise ValidationError(
                f"panel_config.legend_position 值 '{legend_position}' 不合法，"
                f"可选值: {', '.join(sorted(valid_positions))}"
            )
