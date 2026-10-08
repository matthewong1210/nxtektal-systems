"use client";

import { useRef, useState } from "react";

import type {
  GenerationCapability,
  GenerationProjection,
} from "../../lib/staffing";

export interface GenerationStatusProps {
  capability: GenerationCapability;
  generation: GenerationProjection | null;
  disabled: boolean;
  onGenerate(): Promise<unknown>;
}

type CapabilityCopy = {
  message: string;
  unavailable: boolean;
  attention: boolean;
};

function capabilityCopy(capability: GenerationCapability): CapabilityCopy {
  if (capability.status === "DEGRADED_BACKUP_UNCONFIGURED") {
    return {
      message: "海外区域主通道可用，备用通道未配置。",
      unavailable: false,
      attention: true,
    };
  }
  if (capability.status === "UNAVAILABLE") {
    const message = capability.region === null
      ? "尚未配置区域建议服务，仅可手工处理。"
      : capability.region === "CN"
        ? "中国区域建议服务未配置，仅可手工处理。"
        : "海外区域建议服务未配置，仅可手工处理。";
    return { message, unavailable: true, attention: true };
  }
  return {
    message: capability.region === "CN"
      ? "中国区域建议服务可用。"
      : "海外区域建议服务可用，并已配置备用通道。",
    unavailable: false,
    attention: false,
  };
}

function generationCopy(generation: GenerationProjection): string {
  switch (generation.state) {
    case "RESERVED":
      return "建议请求已登记，正在等待处理。";
    case "IN_PROGRESS":
      return "正在生成建议；当前人员异常仍可继续记录。";
    case "RESULT_UNKNOWN":
      return "结果暂时无法确认，请使用“新请求重试建议”重新发起。";
    case "SUCCEEDED":
      return generation.manager_response === null
        ? "建议已生成，需经理确认后才会形成有效排班。"
        : "经理处理结果已保存；现行状态以“有效排班状态”栏为准。";
    case "NO_VALID_SUGGESTION":
      return "没有通过校验的建议，请由经理手工处理。";
    case "UNAVAILABLE":
      return "建议服务当前不可用，请保持现有安排并手工处理。";
    case "REFUSED":
      return "建议请求被服务拒绝，请检查现场信息后手工处理。";
    case "INVALID_RESPONSE":
      return "返回内容未通过校验，未形成排班建议，请手工处理。";
    case "PROVIDER_ERROR":
      return "建议服务返回错误，未形成排班建议，请手工处理。";
    case "CONFIGURATION_ERROR":
      return "建议服务配置不可用，请联系管理员；现场请手工处理。";
    case "SECURITY_ERROR":
      return "建议结果因安全校验被拒绝，未形成排班建议，请手工处理。";
  }
}

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

export function GenerationStatus({
  capability,
  generation,
  disabled,
  onGenerate,
}: GenerationStatusProps) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lock = useRef(false);
  const copy = capabilityCopy(capability);
  const waiting = generation?.state === "RESERVED" || generation?.state === "IN_PROGRESS";
  const uncertain = generation?.state === "RESULT_UNKNOWN";
  const generateDisabled = disabled || copy.unavailable || waiting || uncertain || submitting;

  async function generate(): Promise<void> {
    if (generateDisabled || lock.current) return;
    lock.current = true;
    setSubmitting(true);
    setError(null);
    try {
      await onGenerate();
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      lock.current = false;
      setSubmitting(false);
    }
  }

  return (
    <section className="staffing-generation" aria-label="排班建议状态">
      <div
        className={`staffing-capability${copy.attention ? " staffing-capability-attention" : ""}`}
        role={copy.attention ? "alert" : "status"}
        aria-live="polite"
      >
        <strong>{capability.status}</strong>
        <span>{copy.message}</span>
        {capability.failure_code === null ? null : (
          <span className="staffing-code">{capability.failure_code}</span>
        )}
      </div>

      {generation === null ? (
        <p className="staffing-empty">尚未生成当日排班建议。</p>
      ) : (
        <div className="staffing-generation-state" role="status" aria-live="polite">
          <div className="staffing-generation-heading">
            <strong>{generation.state}</strong>
            {generation.failure_code === null ? null : (
              <span className="staffing-code">{generation.failure_code}</span>
            )}
          </div>
          <p>{generationCopy(generation)}</p>
        </div>
      )}

      <button
        type="button"
        className="btn btn-primary staffing-generate-button"
        disabled={generateDisabled}
        onClick={() => void generate()}
      >
        {submitting ? "正在提交…" : "生成排班建议"}
      </button>
      {error === null ? null : (
        <p className="staffing-error" role="alert">{error}</p>
      )}
    </section>
  );
}
