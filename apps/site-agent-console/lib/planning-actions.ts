/**
 * Binds manager form drafts to planning writes exactly as the panel mounts
 * them: build the frozen wire request from the current context, generate
 * one stable request ID for this attempt, and hand it to the controller.
 */

import {
  buildConfirmationRequest,
  buildInputRequest,
  buildOutcomeRequest,
  buildPlanRequest,
  type InputDraft,
  type OutcomeDraft,
  type PlanDraft,
} from "./planning-forms";
import { newRequestId, type ConfirmationRecord, type PlanRecord, type PlanningContext } from "./planning";
import type { PlanningController } from "./planning-state";

export interface PlanningActions {
  refresh: () => Promise<void>;
  saveInput: (draft: InputDraft, expectedRevision: number) => Promise<void>;
  requestPlan: (
    draft: PlanDraft,
    target: { planId: string | null; expectedPlanVersion: number; inputRevision: number },
  ) => Promise<void>;
  confirmPlan: (plan: PlanRecord, operator: string) => Promise<void>;
  recordOutcome: (
    confirmation: ConfirmationRecord,
    draft: OutcomeDraft,
    supersedesOutcomeId: string | null,
  ) => Promise<void>;
  recover: () => Promise<void>;
  acknowledgeWrite: () => void;
}

export function createPlanningActions(
  controller: PlanningController,
  context: PlanningContext | null,
): PlanningActions {
  const need = (): PlanningContext => {
    if (context === null) throw new Error("The planning context has not loaded yet.");
    return context;
  };
  return {
    refresh: () => controller.refresh(),
    saveInput: async (draft, expectedRevision) => {
      const ctx = need();
      await controller.submit("inputs", buildInputRequest(draft, { requestId: newRequestId("inputs"), expectedRevision, context: ctx }));
    },
    requestPlan: async (draft, target) => {
      const ctx = need();
      await controller.submit(
        "plans",
        buildPlanRequest(draft, {
          requestId: newRequestId("plans"),
          planId: target.planId,
          expectedPlanVersion: target.expectedPlanVersion,
          inputRevision: target.inputRevision,
          timeZone: ctx.site_timezone,
        }),
      );
    },
    confirmPlan: async (plan, operator) => {
      await controller.submit(
        "confirmations",
        buildConfirmationRequest({ requestId: newRequestId("confirmations"), planId: plan.plan_id, planVersion: plan.version, operator }),
      );
    },
    recordOutcome: async (confirmation, draft, supersedesOutcomeId) => {
      const ctx = need();
      if (!confirmation.task_id) throw new Error("No admitted task is linked to this confirmation yet.");
      await controller.submit(
        "outcomes",
        buildOutcomeRequest(draft, {
          requestId: newRequestId("outcomes"),
          confirmationId: confirmation.confirmation_id,
          taskId: confirmation.task_id,
          supersedesOutcomeId,
          timeZone: ctx.site_timezone,
        }),
      );
    },
    recover: async () => {
      await controller.recover();
    },
    acknowledgeWrite: () => controller.acknowledgeWrite(),
  };
}
