import { useEffect, useMemo, useState } from "react";
import {
  discoverLLMModels,
  getLLMConfiguration,
  getLLMProviderPresets,
  saveLLMConfiguration,
  testLLMConfiguration,
  type LLMConfiguration,
  type LLMConfigurationInput,
  type LLMProviderPreset,
} from "../lib/api";

type FormState = Omit<LLMConfigurationInput, "max_tokens"> & {
  max_tokens: string;
};

type ConnectionResult = {
  kind: "idle" | "success" | "error";
  message: string;
  detail?: string;
};

function configurationToForm(configuration: LLMConfiguration): FormState {
  return {
    provider: configuration.provider,
    preset_id: configuration.preset_id,
    api_mode: configuration.api_mode,
    model: configuration.model,
    fast_model: configuration.fast_model,
    reasoning_model: configuration.reasoning_model,
    base_url: configuration.base_url,
    thinking_protocol: configuration.thinking_protocol,
    api_key: null,
    max_tokens: configuration.max_tokens?.toString() ?? "",
  };
}

function toPayload(form: FormState): LLMConfigurationInput {
  return {
    ...form,
    api_key: form.api_key?.trim() || null,
    max_tokens: form.max_tokens ? Number(form.max_tokens) : null,
  };
}

function ProviderCard({
  preset,
  selected,
  onSelect,
}: {
  preset: LLMProviderPreset;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      className="provider-card"
      data-selected={selected}
      onClick={onSelect}
      type="button"
    >
      <span className="provider-card__check" aria-hidden="true">
        {selected ? "✓" : ""}
      </span>
      <strong>{preset.name}</strong>
      <small>
        {preset.api_mode === "anthropic_messages"
          ? "Anthropic Messages"
          : "OpenAI Chat"}
      </small>
      {preset.api_key_configured ? (
        <em>已配置 {preset.api_key_hint}</em>
      ) : null}
      <p>{preset.description}</p>
    </button>
  );
}

export function SettingsPage() {
  const [configuration, setConfiguration] = useState<LLMConfiguration | null>(null);
  const [presets, setPresets] = useState<LLMProviderPreset[]>([]);
  const [form, setForm] = useState<FormState | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState<"save" | "test" | "models" | "">("");
  const [revision, setRevision] = useState(0);
  const [testedRevision, setTestedRevision] = useState<number | null>(null);
  const [result, setResult] = useState<ConnectionResult>({
    kind: "idle",
    message: "修改连接后先测试，再保存为活动配置。",
  });

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      getLLMConfiguration(controller.signal),
      getLLMProviderPresets(controller.signal),
    ])
      .then(([nextConfiguration, nextPresets]) => {
        setConfiguration(nextConfiguration);
        setForm(configurationToForm(nextConfiguration));
        setPresets(nextPresets);
      })
      .catch((cause) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setResult({
          kind: "error",
          message: cause instanceof Error ? cause.message : "配置加载失败",
        });
      });
    return () => controller.abort();
  }, []);

  const selectedPreset = useMemo(
    () => presets.find((preset) => preset.id === form?.preset_id) ?? null,
    [form?.preset_id, presets],
  );

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => (current ? { ...current, [key]: value } : current));
    setRevision((current) => current + 1);
    setTestedRevision(null);
    setResult({ kind: "idle", message: "配置已修改，请重新测试连接。" });
  }

  function selectPreset(preset: LLMProviderPreset) {
    setForm((current) => ({
      provider: preset.provider,
      preset_id: preset.id,
      api_mode: preset.api_mode,
      model: preset.default_model,
      fast_model: preset.fast_model,
      reasoning_model: preset.reasoning_model,
      base_url: preset.base_url,
      thinking_protocol: preset.id === "deepseek" ? "deepseek" : "none",
      api_key: null,
      max_tokens: current?.max_tokens ?? "",
    }));
    setModels([]);
    setRevision((current) => current + 1);
    setTestedRevision(null);
    setResult({
      kind: "idle",
      message: `已选择${preset.name}，请填写这个连接的 API Key 并测试。`,
    });
  }

  async function testConnection() {
    if (!form) return;
    const currentRevision = revision;
    setBusy("test");
    setResult({ kind: "idle", message: "正在验证地址、鉴权和结构化输出…" });
    try {
      const response = await testLLMConfiguration(toPayload(form));
      setTestedRevision(currentRevision);
      setResult({
        kind: "success",
        message: `连接正常 · ${response.model}`,
        detail: `${response.api_mode === "anthropic_messages" ? "Anthropic Messages" : "OpenAI Chat"} · ${response.latency_ms} ms · 已通过结构化 JSON`,
      });
    } catch (cause) {
      setTestedRevision(null);
      setResult({
        kind: "error",
        message: cause instanceof Error ? cause.message : "连接失败",
      });
    } finally {
      setBusy("");
    }
  }

  async function loadModels() {
    if (!form) return;
    setBusy("models");
    setResult({ kind: "idle", message: "正在读取当前账户可用模型…" });
    try {
      const response = await discoverLLMModels(toPayload(form));
      setModels(response.models);
      setResult({
        kind: "success",
        message: `发现 ${response.models.length} 个模型`,
        detail: `${response.latency_ms} ms · 可直接在模型输入框中选择`,
      });
    } catch (cause) {
      setResult({
        kind: "error",
        message: cause instanceof Error ? cause.message : "模型发现失败",
        detail: "仍可按照供应商控制台中的 Model ID 手工填写。",
      });
    } finally {
      setBusy("");
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!form || testedRevision !== revision) return;
    setBusy("save");
    try {
      const next = await saveLLMConfiguration(toPayload(form));
      setConfiguration(next);
      setForm(configurationToForm(next));
      setRevision(0);
      setTestedRevision(0);
      setResult({
        kind: "success",
        message: "配置已加密保存并立即生效",
        detail: `${next.fast_model} / ${next.reasoning_model}`,
      });
    } catch (cause) {
      setResult({
        kind: "error",
        message: cause instanceof Error ? cause.message : "保存失败",
      });
    } finally {
      setBusy("");
    }
  }

  if (!form || !configuration) {
    return <div className="route-pending">{result.message || "配置载入中…"}</div>;
  }

  const canDiscover = selectedPreset?.supports_model_discovery ?? true;
  const hasFreshTest = testedRevision === revision;

  return (
    <section className="model-settings">
      <header className="section-heading model-settings__heading">
        <div>
          <span>模型连接</span>
          <h2>选择供应商，测试后启用</h2>
          <p>模型负责理解与规划；来源证据、权限范围和确定性查询规则不会随模型改变。</p>
        </div>
        <div className="active-connection">
          <span className="status-dot" />
          <span>当前活动</span>
          <strong>{configuration.fast_model}</strong>
          <small>{configuration.api_key_configured ? configuration.api_key_hint : "未配置 Key"}</small>
        </div>
      </header>

      <form onSubmit={submit}>
        <fieldset className="provider-picker">
          <legend>
            <span>步骤 1</span>
            选择连接方式
          </legend>
          <div className="provider-grid">
            {presets.map((preset) => (
              <ProviderCard
                key={preset.id}
                preset={preset}
                selected={preset.id === form.preset_id}
                onSelect={() => selectPreset(preset)}
              />
            ))}
          </div>
        </fieldset>

        <div className="model-settings__columns">
          <fieldset className="connection-panel">
            <legend><span>步骤 2</span>连接信息</legend>
            <label>
              连接名称
              <input
                name="provider"
                value={form.provider}
                onChange={(event) => update("provider", event.target.value)}
              />
              <small>用于运行记录和问题定位，不会发送给模型。</small>
            </label>
            <label>
              接口协议
              <select
                name="api_mode"
                value={form.api_mode}
                onChange={(event) => update(
                  "api_mode",
                  event.target.value as FormState["api_mode"],
                )}
              >
                <option value="openai_chat">OpenAI Chat Completions</option>
                <option value="anthropic_messages">Anthropic Messages</option>
              </select>
            </label>
            <label>
              Base URL
              <input
                name="base_url"
                type="url"
                value={form.base_url}
                onChange={(event) => update("base_url", event.target.value)}
              />
              {selectedPreset?.billing_notice ? (
                <small className="billing-notice">{selectedPreset.billing_notice}</small>
              ) : (
                <small>仅允许公网 HTTPS 地址，保存前会检查解析结果。</small>
              )}
            </label>
            <label>
              API Key
              <input
                name="api_key"
                type="password"
                autoComplete="new-password"
                placeholder={selectedPreset?.api_key_hint ?? "输入新的 API Key"}
                value={form.api_key ?? ""}
                onChange={(event) => update("api_key", event.target.value || null)}
              />
              <small>
                {selectedPreset?.api_key_configured
                  ? "留空沿用这个供应商已加密保存的 Key。"
                  : "只在服务端加密保存，不会返回到浏览器。"}
              </small>
            </label>
          </fieldset>

          <fieldset className="connection-panel">
            <legend><span>步骤 3</span>任务模型</legend>
            <div className="model-discovery">
              <p>使用供应商控制台显示的 Model ID。</p>
              <button
                className="button button--ghost"
                disabled={!canDiscover || busy !== ""}
                onClick={() => void loadModels()}
                type="button"
              >
                {busy === "models" ? "正在读取…" : "发现可用模型"}
              </button>
            </div>
            <datalist id="available-models">
              {models.map((model) => <option key={model} value={model} />)}
            </datalist>
            <label>
              默认模型
              <input
                name="model"
                list="available-models"
                value={form.model}
                onChange={(event) => update("model", event.target.value)}
              />
            </label>
            <label>
              快速识别模型
              <input
                name="fast_model"
                list="available-models"
                value={form.fast_model}
                onChange={(event) => update("fast_model", event.target.value)}
              />
              <small>用于表头识别、模板匹配和普通问题理解。</small>
            </label>
            <label>
              深度推理模型
              <input
                name="reasoning_model"
                list="available-models"
                value={form.reasoning_model}
                onChange={(event) => update("reasoning_model", event.target.value)}
              />
              <small>仅在低置信、结构冲突等复杂任务中调用。</small>
            </label>
            <div className="advanced-model-options">
              <label>
                思考参数
                <select
                  name="thinking_protocol"
                  value={form.thinking_protocol}
                  disabled={form.api_mode === "anthropic_messages"}
                  onChange={(event) => update(
                    "thinking_protocol",
                    event.target.value as FormState["thinking_protocol"],
                  )}
                >
                  <option value="none">不发送供应商特有参数</option>
                  <option value="deepseek">DeepSeek thinking</option>
                </select>
              </label>
              <label>
                最大输出 Tokens
                <input
                  name="max_tokens"
                  type="number"
                  min="128"
                  max="1000000"
                  placeholder="使用模型上限"
                  value={form.max_tokens}
                  onChange={(event) => update("max_tokens", event.target.value)}
                />
              </label>
            </div>
          </fieldset>
        </div>

        <section className="connection-check" data-kind={result.kind} aria-live="polite">
          <div>
            <span>连接检查</span>
            <strong>{result.message}</strong>
            {result.detail ? <small>{result.detail}</small> : null}
          </div>
          <div className="connection-check__actions">
            <button
              className="button button--ghost"
              disabled={busy !== ""}
              onClick={() => void testConnection()}
              type="button"
            >
              {busy === "test" ? "正在测试…" : "测试当前配置"}
            </button>
            <button
              className="button button--primary"
              disabled={!hasFreshTest || busy !== ""}
              type="submit"
            >
              {busy === "save" ? "正在保存…" : "保存并启用"}
            </button>
          </div>
        </section>
      </form>
    </section>
  );
}
