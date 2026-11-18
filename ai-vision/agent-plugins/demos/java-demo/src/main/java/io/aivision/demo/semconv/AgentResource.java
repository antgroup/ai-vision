package io.aivision.demo.semconv;

import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.sdk.resources.Resource;

/**
 * Builds an OTel Resource conforming to entities.yaml (entity.ant.agent).
 *
 * Required resource attributes (violation if missing):
 *   - ant.agent.type
 *   - ant.agent.env
 *   - ant.agent.scene
 *
 * Note: ant.username is a SPAN attribute, not a resource attribute.
 */
public final class AgentResource {

    private AgentResource() {}

    public static final AttributeKey<String> ANT_AGENT_TYPE =
            AttributeKey.stringKey("ant.agent.type");

    public static final AttributeKey<String> ANT_AGENT_PRODUCT =
            AttributeKey.stringKey("ant.agent.product");

    public static final AttributeKey<String> ANT_AGENT_INSTANCE =
            AttributeKey.stringKey("ant.agent.instance");

    public static final AttributeKey<String> ANT_AGENT_SCENE =
            AttributeKey.stringKey("ant.agent.scene");

    public static final AttributeKey<String> ANT_AGENT_ENV =
            AttributeKey.stringKey("ant.agent.env");

    public static final AttributeKey<String> SERVICE_NAME =
            AttributeKey.stringKey("service.name");

    public static final AttributeKey<String> SERVICE_INSTANCE_ID =
            AttributeKey.stringKey("service.instance.id");

    /**
     * Creates the agent resource with all required + recommended fields.
     *
     * @param agentType    e.g. "claudecode", "codex", "openclaw"
     * @param product      e.g. "antclaudecode", maps to service.name
     * @param username     user identifier, e.g. "100001"
     * @param scene        business scene, e.g. "default", "code_review"
     * @param env          deployment env, e.g. "Dev", "Prod", "Default"
     */
    public static Resource create(String agentType, String product,
                                  String username, String scene, String env) {
        String instance = product + "-" + username;
        return Resource.getDefault().merge(Resource.create(Attributes.builder()
                .put(ANT_AGENT_TYPE, agentType)
                .put(ANT_AGENT_PRODUCT, product)
                .put(ANT_AGENT_INSTANCE, instance)
                .put(ANT_AGENT_SCENE, scene)
                .put(ANT_AGENT_ENV, env)
                .put(SERVICE_NAME, product)
                .put(SERVICE_INSTANCE_ID, instance)
                .build()));
    }
}
