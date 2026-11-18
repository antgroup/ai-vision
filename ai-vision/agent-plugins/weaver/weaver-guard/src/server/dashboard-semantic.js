// dashboard-semantic.js — semantic rule matrix construction and tree rendering.

function semanticBucketRank(bucket) {
  const index = FINDING_FIELD_BUCKET_ORDER.indexOf(bucket);
  return index === -1 ? FINDING_FIELD_BUCKET_ORDER.length : index;
}

function semanticFieldBucketOrder(signal = null) {
  return signal === 'metrics' ? METRIC_FIELD_BUCKET_ORDER : FINDING_FIELD_BUCKET_ORDER;
}

function constraintTypeRank(rule) {
  const index = CONSTRAINT_TYPE_ORDER.indexOf(ruleConstraintType(rule));
  return index === -1 ? CONSTRAINT_TYPE_ORDER.length : index;
}

function normalizedSpanKind(value) {
  const normalized = String(value || '').trim().toUpperCase();
  if (!normalized) return '';
  if (normalized === 'TURN') return 'CHAT';
  if (normalized === 'GENERATION') return 'LLM';
  return normalized.replace(/[^A-Z0-9_:-]+/g, '_');
}

function inferSpanKindFromRule(rule) {
  const explicit = normalizedSpanKind(rule?.span_kind);
  if (explicit) return explicit;
  const entity = String(rule?.entity || '').toLowerCase();
  if (entity.startsWith('span:')) {
    const value = entity.slice('span:'.length);
    if (value === 'llm') return 'LLM';
    if (value === 'tool') return 'TOOL';
    if (value === 'turn') return 'CHAT';
    if (value === 'compaction') return 'COMPACTION';
    if (value === 'common' || value === 'base') return 'COMMON';
    return normalizedSpanKind(value);
  }
  return '';
}

function semanticEntityForRule(rule, signal) {
  const entity = String(rule?.entity || '').trim();
  const lower = entity.toLowerCase();

  if (signal === 'events') {
    if (lower.startsWith('resource:')) {
      return { kind: 'resource', key: 'resource', label: 'resource attributes', subtitle: 'events/logs resource attributes', sort: 20 };
    }
    if (lower.startsWith('event:')) {
      const key = entity.slice('event:'.length).trim() || 'event';
      if (key === 'base' || key === 'common') {
        return {
          kind: 'event',
          key: 'common',
          label: '通用 attributes',
          subtitle: 'events/logs common attributes',
          sort: 10,
        };
      }
      return {
        kind: 'event',
        key: normalizedSemanticKey(key),
        label: `event.name: ${key}`,
        subtitle: 'event specific attributes',
        sort: key === 'api_request' ? 40 : 60,
      };
    }
  }

  if (signal === 'traces') {
    if (lower.startsWith('resource:')) {
      return { kind: 'resource', key: 'resource', label: 'resource attributes', subtitle: 'traces resource attributes', sort: 20 };
    }
    const spanKind = inferSpanKindFromRule(rule);
    if (spanKind) {
      if (spanKind === 'COMMON') {
        return {
          kind: 'span',
          key: 'COMMON',
          label: '通用 attributes',
          subtitle: 'span common attributes',
          sort: 10,
        };
      }
      return {
        kind: 'span',
        key: spanKind,
        label: `span.kind: ${spanKind}`,
        subtitle: 'span specific attributes',
        sort: { CHAT: 40, LLM: 50, TOOL: 60, COMPACTION: 70, RESOURCE: 20 }[spanKind] || 80,
      };
    }
  }

  if (signal === 'metrics') {
    if (lower.startsWith('resource:')) {
      return { kind: 'resource', key: 'resource', label: 'Resource attributes', subtitle: 'metrics resource attributes', sort: 20 };
    }
    if (lower.startsWith('metric:')) {
      const key = entity.slice('metric:'.length).trim() || 'metric';
      if (key === 'base' || key === 'common') {
        return { kind: 'metric_common', key: 'common', label: '通用 attributes', subtitle: 'metric common data point attributes', sort: 10 };
      }
      if (key === 'golden') {
        return {
          kind: 'metric_family',
          key: 'golden',
          label: 'RED golden metrics',
          subtitle: '六个 RED 指标通用约束：type / unit / value / error boundary',
          sort: 30,
        };
      }
      if (key === 'api_request' || key === 'llm') {
        return {
          kind: 'metric_family',
          key: 'api_request',
          label: 'API request RED metrics',
          subtitle: 'agent.api_request.count / error.count / duration',
          sort: 40,
        };
      }
      if (key === 'tool') {
        return {
          kind: 'metric_family',
          key: 'tool',
          label: 'Tool RED metrics',
          subtitle: 'agent.tool_call.count / error.count / duration',
          sort: 50,
        };
      }
      return { kind: 'metric_family', key: normalizedSemanticKey(key), label: `metric: ${key}`, subtitle: 'metric specific data point attributes', sort: 40 };
    }
  }

  return {
    kind: 'unclassified',
    key: 'unclassified',
    label: signal === 'metrics' ? 'Metric / 未归类 / 需要补注释' : '未归类 / 需要补注释',
    subtitle: 'missing @weaver.rule.entity or @weaver.rule.span.kind',
    sort: 999,
  };
}

function semanticEntityId(entity) {
  return `${entity?.kind || 'unknown'}:${entity?.key || 'unknown'}`;
}

function semanticEntityArea(entity) {
  return entity?.kind === 'resource' ? 'resource' : 'attributes';
}

function semanticDirectoryBucket(rule, signal = null) {
  if (rule?.semantic_bucket) return rule.semantic_bucket;
  const presence = rulePresenceBucket(rule);
  if (presence) return presence;
  if (signal === 'metrics' && DETAIL_ONLY_CONSTRAINT_TYPES.has(ruleConstraintType(rule))) return 'constraint';
  return '';
}

function semanticBucketStrength(bucket) {
  if (bucket === 'required') return 1;
  if (bucket === 'recommended') return 2;
  if (bucket === 'optional') return 3;
  if (bucket === 'forbidden') return 4;
  return 9;
}

function targetKeyFor(entity, bucket) {
  return `${semanticEntityId(entity)}:${bucket || 'details'}`;
}

function buildPositiveSubjectOwners(rules, signal) {
  const owners = new Map();
  rules.forEach(rule => {
    const bucket = rulePresenceBucket(rule);
    if (bucket !== 'required' && bucket !== 'recommended' && bucket !== 'optional') return;
    const entity = semanticEntityForRule(rule, signal);
    ruleTreeSubjects(rule).forEach(subject => {
      const key = subjectKey(subject);
      if (!key) return;
      if (!owners.has(key)) owners.set(key, []);
      owners.get(key).push({ entity, bucket });
    });
  });
  owners.forEach((items, key) => {
    const deduped = Array.from(new Map(items.map(item => [targetKeyFor(item.entity, item.bucket), item])).values())
      .sort((a, b) =>
        semanticBucketStrength(a.bucket) - semanticBucketStrength(b.bucket) ||
        (a.entity.sort || 0) - (b.entity.sort || 0) ||
        a.entity.label.localeCompare(b.entity.label)
      );
    owners.set(key, deduped);
  });
  return owners;
}

function targetEntriesForRuleSubject(rule, signal, subject, positiveOwners) {
  const metricEntities = metricScopeEntitiesForRule(rule, signal);
  if (metricEntities.length) {
    const bucket = rulePresenceBucket(rule) || semanticDirectoryBucket(rule, signal);
    return metricEntities.map(entity => ({ entity, bucket }));
  }
  const ruleEntity = semanticEntityForRule(rule, signal);
  const ruleBucket = rulePresenceBucket(rule);
  const owners = positiveOwners.get(subjectKey(subject)) || [];
  if (ruleBucket === 'required' || ruleBucket === 'recommended' || ruleBucket === 'optional') {
    return [{ entity: ruleEntity, bucket: ruleBucket }];
  }
  if (ruleBucket === 'forbidden') {
    const oppositeAreaOwners = owners.filter(owner => semanticEntityArea(owner.entity) !== semanticEntityArea(ruleEntity));
    if (oppositeAreaOwners.length) return oppositeAreaOwners;
    return [{ entity: ruleEntity, bucket: 'forbidden' }];
  }
  if (owners.length) return owners.map(owner => ({ entity: owner.entity, bucket: '' }));
  return [{ entity: ruleEntity, bucket: '' }];
}

function semanticConditionForRule(rule, entity) {
  const rawKey = String(rule?.condition_key || '').trim();
  const rawLabel = String(rule?.condition_zh || '').trim();
  if (rawKey || rawLabel) {
    const key = normalizedSemanticKey(rawKey || rawLabel);
    return { key, label: rawLabel || SEMANTIC_CONDITION_LABELS[key] || rawKey || '条件约束' };
  }
  if (entity.kind === 'event' && entity.key === 'api_request') {
    return { key: 'unclassified', label: SEMANTIC_CONDITION_LABELS.unclassified };
  }
  if (entity.kind === 'unclassified') {
    return { key: 'unclassified', label: SEMANTIC_CONDITION_LABELS.unclassified };
  }
  return { key: 'common', label: SEMANTIC_CONDITION_LABELS.common };
}

function semanticConditionRank(condition) {
  const index = SEMANTIC_CONDITION_ORDER.indexOf(condition?.key);
  return index === -1 ? SEMANTIC_CONDITION_ORDER.length : index;
}

function ruleDisplayOrder(rule) {
  const order = Number(rule?.display_order);
  if (Number.isFinite(order)) return order;
  return semanticBucketRank(rulePresenceBucket(rule)) * 100 + constraintTypeRank(rule) * 10 + findingLevelRank(rule?.level);
}

function semanticMetadataIssues(rule, signal, entity, condition) {
  const issues = [];
  if (!rule?.entity) issues.push('缺少 @weaver.rule.entity');
  const subjects = ruleSubjects(rule);
  const type = ruleConstraintType(rule);
  if (!subjects.length) {
    if (ruleFields(rule).length) {
      issues.push('@weaver.rule.fields 仅作兼容；建议补 @weaver.rule.subject');
    } else {
      issues.push('缺少 @weaver.rule.subject');
    }
  }
  if (!type) issues.push('缺少 @weaver.rule.constraint.type');
  if (type === 'enum' && !(Array.isArray(rule?.allowed_values) && rule.allowed_values.length)) {
    issues.push('enum 约束缺少 @weaver.rule.allowed_values');
  }
  if (type === 'fixed_value' && !rule?.expected_value) {
    issues.push('fixed_value 约束缺少 @weaver.rule.expected_value');
  }
  if (signal === 'traces' && entity.kind === 'span' && !rule?.span_kind && !String(rule?.entity || '').toLowerCase().startsWith('span:')) {
    issues.push('缺少 @weaver.rule.span.kind');
  }
  if (entity.kind === 'event' && entity.key === 'api_request' && condition.key === 'unclassified') {
    issues.push('缺少 @weaver.rule.condition.*');
  }
  return issues;
}

function buildSemanticRuleMatrix(rules, signal) {
  const groups = new Map();
  const positiveOwners = buildPositiveSubjectOwners(rules, signal);

  function ensureGroup(entity) {
    const entityKey = semanticEntityId(entity);
    if (!groups.has(entityKey)) {
      groups.set(entityKey, { ...entity, id: entityKey, rules: [], conditions: new Map() });
    }
    return groups.get(entityKey);
  }

  function addRuleTarget(rule, entity, bucket, subjects) {
    const group = ensureGroup(entity);
    const condition = semanticConditionForRule(rule, entity);
    const conditionKey = condition.key;
    if (!group.conditions.has(conditionKey)) {
      group.conditions.set(conditionKey, {
        ...condition,
        rules: [],
        buckets: new Map(semanticFieldBucketOrder(signal).map(bucket => [bucket, []])),
      });
    }
    const conditionGroup = group.conditions.get(conditionKey);
    const enrichedRule = {
      ...rule,
      semantic_bucket: bucket,
      semantic_subjects: subjects.map(normalizeRuleSubject).filter(Boolean),
      semantic_target_metric: group.metric_name || '',
      semantic_entity: group,
      semantic_condition: conditionGroup,
      semantic_issues: semanticMetadataIssues(rule, signal, group, condition),
    };
    group.rules.push(enrichedRule);
    conditionGroup.rules.push(enrichedRule);
    if (bucket) {
      if (!conditionGroup.buckets.has(bucket)) conditionGroup.buckets.set(bucket, []);
      conditionGroup.buckets.get(bucket).push(enrichedRule);
    }
  }

  rules.forEach(rule => {
    const subjects = ruleTreeSubjects(rule);
    if (!subjects.length) {
      const metricEntities = metricScopeEntitiesForRule(rule, signal);
      if (metricEntities.length) {
        const bucket = rulePresenceBucket(rule) || semanticDirectoryBucket(rule, signal);
        metricEntities.forEach(entity => addRuleTarget(rule, entity, bucket, []));
      } else {
        const entity = semanticEntityForRule(rule, signal);
        addRuleTarget(rule, entity, rulePresenceBucket(rule), []);
      }
      return;
    }
    const targets = new Map();
    subjects.forEach(subject => {
      targetEntriesForRuleSubject(rule, signal, subject, positiveOwners).forEach(target => {
        const key = targetKeyFor(target.entity, target.bucket);
        if (!targets.has(key)) targets.set(key, { entity: target.entity, bucket: target.bucket, subjects: [] });
        targets.get(key).subjects.push(subject);
      });
    });
    targets.forEach(target => addRuleTarget(rule, target.entity, target.bucket, target.subjects));
  });

  return Array.from(groups.values())
    .map(group => ({
      ...group,
      conditions: Array.from(group.conditions.values()).sort((a, b) =>
        semanticConditionRank(a) - semanticConditionRank(b) ||
        a.label.localeCompare(b.label)
      ),
    }))
    .sort((a, b) => a.sort - b.sort || a.label.localeCompare(b.label));
}

function semanticSelectableKey(group) {
  return `semantic:${group?.kind || 'unknown'}:${group?.key || 'unknown'}`;
}

function semanticFieldSelectableKey(group, bucket, label) {
  return `semantic-field:${group?.id || semanticSelectableKey(group)}:${bucket}:${encodeURIComponent(String(label || 'field'))}`;
}

function semanticFieldLevel(bucket) {
  return bucket === 'metric_dimensions'
    ? 'violation'
    : bucket === 'metric_dimension_constraints'
    ? 'violation'
    : bucket === 'metric_constraints'
    ? 'violation'
    : bucket === 'metric_datapoint_constraints'
    ? 'violation'
    : bucket === 'metric_forbidden_dimensions'
    ? 'violation'
    : bucket === 'recommended'
    ? 'improvement'
    : bucket === 'required' || bucket === 'forbidden' || bucket === 'constraint'
      ? 'violation'
      : 'muted';
}

function uniqueRulesFromRows(rows) {
  return Array.from(new Map(rows.map(row => [ruleSelectableKey(row.rule), row.rule])).values());
}

function semanticFieldAffected(field) {
  return uniqueRulesFromRows(field.rows).reduce((sum, rule) => sum + countAffected(rule), 0);
}

function semanticFieldMetricScopes(field) {
  if (field?.group?.metric_name) return [field.group.metric_name];
  return uniqueValues((field?.rows || []).flatMap(row => row.rule?.semantic_target_metric ? [row.rule.semantic_target_metric] : ruleRedMetricScopes(row.rule)));
}

function semanticRowMetricScopes(row) {
  if (row?.rule?.semantic_target_metric) return [row.rule.semantic_target_metric];
  return ruleRedMetricScopes(row?.rule);
}

function isMetricNameGroup(group) {
  return group?.kind === 'metric_name';
}

function semanticFieldDisplayBucketLabel(field) {
  if (METRIC_LANE_LABELS[field?.bucket]) return METRIC_LANE_LABELS[field.bucket];
  return FINDING_FIELD_BUCKET_LABELS[field?.bucket] || '字段';
}

function semanticFieldDisplayHint(field) {
  if (METRIC_LANE_HINTS[field?.bucket]) return METRIC_LANE_HINTS[field.bucket];
  return SEMANTIC_BUCKET_HINTS[field?.bucket] || '该字段来自 Rego 注释声明。';
}

function semanticMetricLaneSelectableKey(group, lane, field) {
  const identityKey = isExactRedMetricSubject(field?.subject) ? 'metric-identity' : '';
  const key = identityKey || subjectKey(field?.subject) || field?.label || 'field';
  return `semantic-field:${group?.id || semanticSelectableKey(group)}:${lane}:${encodeURIComponent(key)}`;
}

function mergeFieldConditions(target, source, rowLabel = null, rowIsField = null) {
  source.conditions.forEach((conditionRows, key) => {
    if (!target.conditions.has(key)) {
      target.conditions.set(key, { condition: conditionRows.condition, rows: [] });
    }
    const targetCondition = target.conditions.get(key);
    conditionRows.rows.forEach(row => {
      const rowKey = `${key}:${ruleSelectableKey(row.rule)}:${subjectKey(row.subject)}`;
      if (target.rowKeys.has(rowKey)) return;
      target.rowKeys.add(rowKey);
      const nextRow = {
        ...row,
        label: rowLabel || row.label,
        isField: rowIsField === null ? row.isField : rowIsField,
      };
      target.rows.push(nextRow);
      targetCondition.rows.push(nextRow);
    });
  });
}

function metricRulePlacements(field) {
  return uniqueValues((field?.rows || []).map(row => row?.rule?.placement).filter(Boolean));
}

function metricLaneForField(field) {
  const subject = normalizeRuleSubject(field?.subject);
  const subjectValue = subject?.value || field?.label || '';
  const bucket = field?.bucket || '';
  const placements = metricRulePlacements(field);

  if (isExactRedMetricSubject(subject)) return 'metric_constraints';
  if (bucket === 'forbidden') return 'metric_forbidden_dimensions';
  if (placements.includes('metric_data_point')) return 'metric_datapoint_constraints';
  if (placements.includes('metric')) return 'metric_constraints';
  if (placements.includes('metric_data_point_attributes')) {
    if (bucket === 'required') return 'metric_dimensions';
    return 'metric_dimension_constraints';
  }
  if (subjectValue.startsWith('metric.data_point.')) return 'metric_datapoint_constraints';
  if (subjectValue.startsWith('metric.')) return 'metric_constraints';
  if (bucket === 'required') return 'metric_dimensions';
  return 'metric_dimension_constraints';
}

function metricLaneFieldLabel(field) {
  return isExactRedMetricSubject(field?.subject) ? METRIC_IDENTITY_CONSTRAINT_LABEL : field.label;
}

function semanticMetricLaneGroups(group) {
  const lanes = new Map(METRIC_LANE_ORDER.map(lane => [lane, {
    key: lane,
    label: METRIC_LANE_LABELS[lane],
    hint: METRIC_LANE_HINTS[lane],
    fields: [],
    fieldMap: new Map(),
  }]));

  semanticFieldBuckets(group, 'metrics').flatMap(bucket => bucket.fields).forEach(field => {
    const lane = metricLaneForField(field);
    const laneGroup = lanes.get(lane);
    if (!laneGroup) return;
    const label = metricLaneFieldLabel(field);
    const isMetricIdentity = isExactRedMetricSubject(field.subject);
    const key = semanticMetricLaneSelectableKey(group, lane, field);
    if (!laneGroup.fieldMap.has(key)) {
      laneGroup.fieldMap.set(key, {
        ...field,
        key,
        label,
        bucket: lane,
        metric_lane: lane,
        group,
        rows: [],
        rowKeys: new Set(),
        conditions: new Map(),
        isField: isMetricIdentity ? false : field.isField,
        source_buckets: new Set(),
      });
    }
    const target = laneGroup.fieldMap.get(key);
    target.source_buckets.add(field.bucket);
    mergeFieldConditions(target, field, label, isMetricIdentity ? false : field.isField);
  });

  return METRIC_LANE_ORDER.map(lane => {
    const laneGroup = lanes.get(lane);
    const fields = Array.from(laneGroup.fieldMap.values())
      .map(field => {
        field.bucket_labels = Array.from(field.source_buckets)
          .map(bucket => FINDING_FIELD_BUCKET_LABELS[bucket] || METRIC_LANE_LABELS[bucket] || bucket)
          .filter(Boolean);
        delete field.source_buckets;
        delete field.rowKeys;
        return field;
      })
      .sort((a, b) =>
        Number(a.label !== METRIC_IDENTITY_CONSTRAINT_LABEL) - Number(b.label !== METRIC_IDENTITY_CONSTRAINT_LABEL) ||
        a.label.localeCompare(b.label)
      );
    return {
      key: laneGroup.key,
      label: laneGroup.label,
      hint: laneGroup.hint,
      fields,
    };
  });
}

function semanticMetricLaneFields(group) {
  return semanticMetricLaneGroups(group).flatMap(lane => lane.fields);
}

function semanticFieldBuckets(group, signal = null) {
  const bucketOrder = semanticFieldBucketOrder(signal);
  const buckets = new Map(bucketOrder.map(bucket => [bucket, new Map()]));
  const fieldsBySubject = new Map();

  function ensureField(bucket, subject) {
    const label = subjectDisplayLabel(subject);
    const fieldKey = semanticFieldSelectableKey(group, bucket, label);
    const bucketFields = buckets.get(bucket);
    if (!bucketFields.has(fieldKey)) {
      bucketFields.set(fieldKey, {
        key: fieldKey,
        label,
        bucket,
        group,
        rows: [],
        rowKeys: new Set(),
        conditions: new Map(),
        subject: normalizeRuleSubject(subject),
        subjectKey: subjectKey(subject),
        isField: normalizeRuleSubject(subject)?.kind === 'field',
      });
      const key = subjectKey(subject);
      if (!fieldsBySubject.has(key)) fieldsBySubject.set(key, []);
      fieldsBySubject.get(key).push(bucketFields.get(fieldKey));
    }
    return bucketFields.get(fieldKey);
  }

  function addRow(field, rule, condition, subject) {
    const rowKey = `${condition.key}:${ruleSelectableKey(rule)}:${subjectKey(subject)}`;
    if (field.rowKeys.has(rowKey)) return;
    field.rowKeys.add(rowKey);
    const row = {
      label: field.label,
      rule,
      condition,
      subject: normalizeRuleSubject(subject),
      isField: normalizeRuleSubject(subject)?.kind === 'field',
    };
    field.rows.push(row);
    if (!field.conditions.has(condition.key)) {
      field.conditions.set(condition.key, { condition, rows: [] });
    }
    field.conditions.get(condition.key).rows.push(row);
  }

  (group?.conditions || []).forEach(condition => {
    (condition.rules || []).forEach(rule => {
      const bucket = semanticDirectoryBucket(rule, signal);
      if (!bucket) return;
      const subjects = rule.semantic_subjects || ruleTreeSubjects(rule);
      subjects.forEach(subject => {
        const field = ensureField(bucket, subject);
        addRow(field, rule, condition, subject);
      });
    });
  });

  (group?.conditions || []).forEach(condition => {
    (condition.rules || []).forEach(rule => {
      (rule.semantic_subjects || ruleSubjects(rule)).forEach(subject => {
        const matchedFields = fieldsBySubject.get(subjectKey(subject)) || [];
        matchedFields.forEach(field => addRow(field, rule, condition, subject, false));
      });
    });
  });

  return bucketOrder.map(bucket => ({
    bucket,
    fields: Array.from((buckets.get(bucket) || new Map()).values())
      .map(field => {
        delete field.rowKeys;
        return field;
      })
      .sort((a, b) => a.label.localeCompare(b.label)),
  }));
}

function semanticFieldsForGroups(groups, signal = null) {
  return groups.flatMap(group => signal === 'metrics' && isMetricNameGroup(group)
    ? semanticMetricLaneFields(group)
    : semanticFieldBuckets(group, signal).flatMap(bucket => bucket.fields));
}

function renderSemanticFieldLeaf(field, bucket, activeKey, containerId, signal, subtitle = null) {
  const rules = uniqueRulesFromRows(field.rows);
  const metricScope = signal === 'metrics' ? compactRedMetricScope(semanticFieldMetricScopes(field)) : '';
  const labelSubtitle = subtitle || metricScope || `${rules.length} rules`;
  return `<button class="tree-leaf semantic-field-leaf ${field.key === activeKey ? 'active' : ''} ${esc(semanticFieldLevel(bucket))}" type="button" data-select-rule-tree="${esc(field.key)}" data-rules-container="${esc(containerId || '')}" data-rules-signal="${esc(signal || '')}" title="${esc(`${field.label}${metricScope ? ` · ${metricScope}` : ''}`)}">
    <span class="tree-leaf-main">
      <span class="tree-leaf-title">${esc(field.label)}</span>
      <small>${esc(labelSubtitle)}</small>
    </span>
    ${treeCountBadge(rules.length, semanticFieldLevel(bucket))}
  </button>`;
}

function renderSemanticMetricGroupNode(group, activeKey, containerId, signal) {
  const laneGroups = semanticMetricLaneGroups(group);
  const fieldCount = laneGroups.reduce((sum, lane) => sum + lane.fields.length, 0);
  const issueCount = group.rules.reduce((sum, rule) => sum + (rule.semantic_issues?.length || 0), 0);
  const entityOpenKey = `rule:${containerId}:${signal || 'all'}:${group.id}`;
  const info = RED_METRIC_INFO[group.metric_name] || {};
  const laneHtml = laneGroups
    .filter(lane => lane.fields.length)
    .map(lane => {
      const laneOpenKey = `${entityOpenKey}:${lane.key}`;
      const leaves = lane.fields.map(field => {
        const bucketSummary = field.bucket_labels?.length
          ? `${field.bucket_labels.join(' / ')} · ${uniqueRulesFromRows(field.rows).length} rules`
          : `${uniqueRulesFromRows(field.rows).length} rules`;
        return renderSemanticFieldLeaf(field, lane.key, activeKey, containerId, signal, bucketSummary);
      }).join('');
      return `<details class="tree-domain semantic-metric-lane-node" data-tree-open-key="${esc(laneOpenKey)}" ${treeOpen(state.expandedRuleTrees, laneOpenKey) ? 'open' : ''}>
        <summary>
          <span class="tree-domain-name">${esc(lane.label)}</span>
          <span class="tree-domain-meta">${treeCountBadge(lane.fields.length, semanticFieldLevel(lane.key))}</span>
        </summary>
        <div class="tree-leaves">${leaves}</div>
      </details>`;
    }).join('');
  return `<details class="tree-dir semantic-object-node metric-name-node" data-tree-open-key="${esc(entityOpenKey)}" ${treeOpen(state.expandedRuleTrees, entityOpenKey) ? 'open' : ''}>
    <summary>
      <span class="tree-dir-label">
        <span class="tree-dir-name">${esc(group.label)}</span>
        <small>${esc(`${info.family || 'Metric'} · ${info.red || 'RED'} · ${info.shape || 'metric'} · ${info.unit || ''}`)}</small>
      </span>
      <span class="tree-dir-actions">${treeCountBadge(fieldCount)}${issueCount ? treeCountBadge(issueCount, 'warning') : ''}</span>
    </summary>
    <div class="tree-domains">${laneHtml || '<div class="tree-empty">暂无 metric 约束</div>'}</div>
  </details>`;
}

function renderSemanticRuleTree(groups, activeKey, containerId, signal) {
  const treeTitle = signal === 'metrics' ? 'Metrics 语义规范' : '字段目录';
  const treeHint = signal === 'metrics'
    ? '通用 attributes / Resource attributes / metric name / 维度 / 约束'
    : '字段类型 / Rego 规则组 / 字段名';
  const groupNodes = groups.map(group => {
    if (signal === 'metrics' && isMetricNameGroup(group)) {
      return renderSemanticMetricGroupNode(group, activeKey, containerId, signal);
    }
    const issueCount = group.rules.reduce((sum, rule) => sum + (rule.semantic_issues?.length || 0), 0);
    const fieldBuckets = semanticFieldBuckets(group, signal);
    const fieldCount = fieldBuckets.reduce((sum, bucket) => sum + bucket.fields.length, 0);
    const entityOpenKey = `rule:${containerId}:${signal || 'all'}:${group.id}`;
    const bucketHtml = fieldBuckets.map(({ bucket, fields }) => {
      const bucketOpenKey = `${entityOpenKey}:${bucket}`;
      const leaves = fields.length ? fields.map(field => {
        return renderSemanticFieldLeaf(field, bucket, activeKey, containerId, signal);
      }).join('') : '<div class="tree-empty">暂无字段</div>';
      return `<details class="tree-domain semantic-bucket-node" data-tree-open-key="${esc(bucketOpenKey)}" ${treeOpen(state.expandedRuleTrees, bucketOpenKey) ? 'open' : ''}>
        <summary>
          <span class="tree-domain-name">${esc(FINDING_FIELD_BUCKET_LABELS[bucket])}</span>
          <span class="tree-domain-meta">${treeCountBadge(fields.length, semanticFieldLevel(bucket))}</span>
        </summary>
        <div class="tree-leaves">${leaves}</div>
      </details>`;
    }).join('');
    return `<details class="tree-dir semantic-object-node" data-tree-open-key="${esc(entityOpenKey)}" ${treeOpen(state.expandedRuleTrees, entityOpenKey) ? 'open' : ''}>
      <summary>
        <span class="tree-dir-name">${esc(group.label)}</span>
        <span class="tree-dir-actions">${treeCountBadge(fieldCount)}${issueCount ? treeCountBadge(issueCount, 'warning') : ''}</span>
      </summary>
      <div class="tree-domains">${bucketHtml}</div>
    </details>`;
  }).join('');
  return `<aside class="split-tree rule-tree semantic-rule-tree compact-tree">
    <div class="tree-head compact">
      <div><b>${esc(treeTitle)}</b><small>${esc(treeHint)}</small></div>
      ${treeCountBadge(groups.reduce((sum, group) => sum + group.rules.length, 0))}
    </div>
    ${groupNodes || '<div class="empty">No Rego rules loaded for this signal.</div>'}
  </aside>`;
}

function renderSemanticRuleRow(row, groupEnabled, signal = null) {
  const rule = row.rule;
  const ruleKey = ruleSelectableKey(rule);
  const canToggleRule = groupEnabled;
  const affected = countAffected(rule);
  const issues = rule.semantic_issues || [];
  const issueHtml = issues.length ? `<div class="meta-line metadata-warning">${esc(issues.join('；'))}</div>` : '';
  const metricScopeHtml = signal === 'metrics' ? redMetricScopeChipsHtml(semanticRowMetricScopes(row)) : '';
  return `<div class="field-rule-row ${esc(levelClass(rule.level))} ${rule.enabled ? '' : 'disabled'}">
    <div class="field-rule-main">
      <div class="field-rule-title">
        ${row.isField ? `<code>${esc(row.label)}</code>` : `<span>${esc(row.label)}</span>`}
        ${ruleConstraintChipHtml(rule)}
        ${rule.enabled ? badge(rule.level) : badge('disabled', groupEnabled ? 'disabled' : 'group disabled')}
      </div>
      <div class="meta-line">${esc(rule.id)} · ${esc(rule.file)}:${esc(rule.line)} · affected ${esc(affected)}</div>
      <p>${esc(rulePurposeText(rule))}</p>
      ${ruleConstraintSummaryHtml(rule)}
      ${metricScopeHtml}
      ${row.isField ? '' : `<div class="field-chip-row compact">${ruleFieldChipsHtml(rule)}</div>`}
      ${issueHtml}
    </div>
    <div class="field-rule-actions">
      <button class="btn btn-outline" type="button" data-rule-id="${esc(ruleKey)}" data-rule-group="${esc(rule.rule_group || 'default')}" data-rule-enabled="${esc(!rule.enabled)}" ${canToggleRule ? '' : 'disabled'}>${canToggleRule ? (rule.enabled ? '关闭' : '启用') : '上级已关闭'}</button>
      <button class="btn btn-secondary" type="button" data-copy-rule-fix="${esc(ruleKey)}">复制 prompt</button>
      <button class="btn btn-ghost" type="button" data-rule-findings="${esc(ruleKey)}">Findings</button>
    </div>
    <details class="debug-source compact">
      <summary>规则说明与 Rego source</summary>
      ${ruleDocHtml(rule, false, signal)}
      <pre class="rule-source">${esc(rule.source || 'no source available')}</pre>
    </details>
  </div>`;
}

function renderSemanticRuleDetail(field, groupEnabled, signal = null) {
  if (!field) return '<section class="split-detail"><div class="empty">请选择左侧字段。</div></section>';
  const group = field.group;
  const rules = uniqueRulesFromRows(field.rows);
  const affected = semanticFieldAffected(field);
  const metadataIssues = rules.reduce((sum, rule) => sum + (rule.semantic_issues?.length || 0), 0);
  const metricScopes = signal === 'metrics' ? semanticFieldMetricScopes(field) : [];
  const conditions = Array.from(field.conditions.values())
    .sort((a, b) => semanticConditionRank(a.condition) - semanticConditionRank(b.condition) || a.condition.label.localeCompare(b.condition.label));
  const conditionHtml = conditions.map(({ condition, rows }) => {
    const sortedRows = rows.slice().sort((a, b) =>
      ruleDisplayOrder(a.rule) - ruleDisplayOrder(b.rule) ||
      String(a.rule.id || a.rule.rule).localeCompare(String(b.rule.id || b.rule.rule))
    );
    return `<section class="field-matrix-condition">
      <header class="condition-head">
        <div>
          <span class="section-kicker">${esc(condition.key)}</span>
          <h3>${esc(condition.label)}</h3>
          <p>${esc(sortedRows.length)} rules · ${esc(group.label)}</p>
        </div>
      </header>
      <div class="field-rule-list field-page-rule-list">${sortedRows.map(row => renderSemanticRuleRow(row, groupEnabled, signal)).join('')}</div>
    </section>`;
  }).join('');
  return `<section class="split-detail rule-detail-panel semantic-rule-detail">
    <header class="detail-panel-head directory field-page-head">
      <div>
        ${groupEnabled ? badge('ok', 'active') : badge('disabled', 'policy group disabled')}
        <span class="rule-code">${esc(semanticFieldDisplayBucketLabel(field))}</span>
        <h3><code>${esc(field.label)}</code></h3>
        <p>${esc(semanticFieldDisplayHint(field))}</p>
        ${redMetricScopeSummaryHtml(metricScopes)}
        <div class="meta-line">${esc(group.label)} · ${esc(group.subtitle)} · rules ${esc(rules.length)}${affected ? ` · affected ${esc(affected)}` : ''}${metadataIssues ? ` · metadata gaps ${esc(metadataIssues)}` : ''}</div>
      </div>
    </header>
    ${conditionHtml || '<div class="empty">该字段没有可展示规则。</div>'}
  </section>`;
}

function ruleSelectableKey(rule) {
  return rule?.key || `${rule?.rule_group || 'default'}:${rule?.policy_dir || 'unknown'}:${rule?.group || 'unknown'}:${rule?.id || rule?.rule || 'rule'}`;
}

function compactLevelMark(level) {
  const normalized = levelClass(level);
  const label = normalized === 'violation' ? 'V' : normalized === 'improvement' ? 'R' : normalized === 'information' ? 'I' : normalized === 'semconv' ? 'S' : '?';
  return `<span class="level-mark ${esc(normalized)}">${label}</span>`;
}

function renderRules(containerId = 'rulesModalBody', signal = null) {
  const policyEntries = policyGroupEntries();
  if (!policyEntries.length) {
    setHtmlIfChanged(containerId, '<div class="empty">No Rego policy groups loaded.</div>');
    return;
  }

  let activeGroup = state.activeRuleGroups[containerId];
  if (!policyEntries.some(([name]) => name === activeGroup)) activeGroup = policyEntries[0][0];
  state.activeRuleGroups[containerId] = activeGroup;

  const activePolicyGroup = policyEntries.find(([name]) => name === activeGroup)?.[1];
  const groupEnabled = activePolicyGroup.enabled !== false;
  const visibleRules = sectionEntriesForPolicyGroup(activePolicyGroup, signal)
    .flatMap(([, section]) => rulesForSection(section, signal));
  const groups = buildSemanticRuleMatrix(visibleRules, signal);
  let activeKey = state.activeRuleSelections[containerId];
  const fields = semanticFieldsForGroups(groups, signal);
  let activeField = fields.find(field => field.key === activeKey);
  if (!activeField && activeKey?.startsWith('semantic:')) {
    const previousGroup = groups.find(group => semanticSelectableKey(group) === activeKey);
    activeField = fields.find(field => field.group.id === previousGroup?.id);
  }
  if (!activeField && fields.length) {
    activeField = fields[0];
    activeKey = activeField.key;
  }
  if (!activeField) activeKey = '';
  state.activeRuleSelections[containerId] = activeKey;
  const stats = ruleStats(visibleRules);
  const signalLabel = signal ? SIGNALS[signal].label : 'All Signals';
  const treeScroll = captureRuleTreeScroll(containerId);
  const organizationText = signal === 'metrics'
    ? 'Metrics 按层级展示：通用 attributes、Resource attributes、具体 metric name，以及该 metric 的维度、维度约束、指标约束、数据点约束、禁止维度。规范外指标不进入本 RED 约束视图，也不会因此产生 findings。'
    : `按字段所属区域组织：通用 attributes、resource attributes、span/event/metric specific；区域内按字段主 presence 组展示 ${FINDING_FIELD_BUCKET_LABELS.required} / ${FINDING_FIELD_BUCKET_LABELS.recommended} / ${FINDING_FIELD_BUCKET_LABELS.forbidden}；错位禁止规则归并到字段主目录详情。`;

  const groupTabs = policyEntries.map(([name, policyGroup]) => {
    const enabled = policyGroup.enabled !== false;
    return `<button class="tab ${name === activeGroup ? 'active' : ''}" type="button" data-rules-policy-tab="${esc(name)}" data-rules-container="${esc(containerId)}" data-rules-signal="${esc(signal || '')}">
      ${esc(policyGroupLabel(name))} <span class="tab-count">${esc(policyGroup.enabled_rules ?? 0)}/${esc(policyGroup.total ?? 0)}</span>${enabled ? '' : ' · off'}
    </button>`;
  }).join('');

  setHtmlIfChanged(containerId, `<section class="rules-browser" data-rules-browser="${esc(containerId)}" data-rules-signal="${esc(signal || '')}">
    <div class="rule-tab-row">
      <div>
        <div class="section-kicker">Policy group</div>
        <nav class="tabs rule-tabs" aria-label="Policy groups">${groupTabs}</nav>
      </div>
      ${switchButton('policy group', groupEnabled, {
        'data-policy-group-toggle': activeGroup,
        'data-policy-group-enabled': String(!groupEnabled),
      })}
    </div>
    <section class="rule-group rule-group-split">
      <div class="rule-group-head">
        <div class="rule-group-title-row">
          <div>
            <h2>${esc(policyGroupLabel(activeGroup))} / ${esc(signalLabel)}</h2>
            <p>${stats.enabled}/${stats.total} enabled · policy group: ${esc(activeGroup)} · source: Rego policy</p>
            <p>${esc(organizationText)}</p>
            <p>${esc(activePolicyGroup?.readme_summary_zh || policy_group_readme_summary(activeGroup))}</p>
          </div>
          <div class="badge ${groupEnabled ? 'badge-ok' : 'badge-disabled'}">${groupEnabled ? 'active' : 'disabled'}</div>
        </div>
      </div>
      <div class="split-browser rule-browser">
        ${renderSemanticRuleTree(groups, activeKey, containerId, signal)}
        ${renderSemanticRuleDetail(activeField, groupEnabled, signal)}
      </div>
    </section>
  </section>`);
  restoreRuleTreeScroll(containerId, treeScroll);
}

function policy_group_readme_summary(groupName) {
  return groupName === 'aivision'
    ? 'AI Vision 规则组：面向 AI agent 遥测的内置语义规则集合。'
    : `${groupName || 'default'} 规则组：自定义语义规则集合。`;
}
