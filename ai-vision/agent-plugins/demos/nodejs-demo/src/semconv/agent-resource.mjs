import { Resource } from '@opentelemetry/resources';

const ANT_AGENT_TYPE = 'ant.agent.type';
const ANT_AGENT_PRODUCT = 'ant.agent.product';
const ANT_AGENT_INSTANCE = 'ant.agent.instance';
const ANT_AGENT_SCENE = 'ant.agent.scene';
const ANT_AGENT_ENV = 'ant.agent.env';
const SERVICE_NAME = 'service.name';
const SERVICE_INSTANCE_ID = 'service.instance.id';

/**
 * Creates an OTel Resource conforming to entities.yaml (entity.ant.agent).
 *
 * @param {string} agentType  e.g. "claudecode", "codex", "openclaw"
 * @param {string} product    e.g. "antclaudecode", maps to service.name
 * @param {string} username   user identifier, e.g. "100001"
 * @param {string} scene      business scene, e.g. "default", "code_review"
 * @param {string} env        deployment env, e.g. "Dev", "Prod", "Default"
 */
export function createAgentResource(agentType, product, username, scene, env) {
  const instance = `${product}-${username}`;
  return new Resource({
    [ANT_AGENT_TYPE]: agentType,
    [ANT_AGENT_PRODUCT]: product,
    [ANT_AGENT_INSTANCE]: instance,
    [ANT_AGENT_SCENE]: scene,
    [ANT_AGENT_ENV]: env,
    [SERVICE_NAME]: product,
    [SERVICE_INSTANCE_ID]: instance,
  });
}
