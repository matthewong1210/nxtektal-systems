import {
  newStaffingRequestId,
  type ExceptionCorrectRequest,
  type ExceptionRecordRequest,
  type ExceptionReplacementInput,
  type ManagerPatchOperation,
  type ManagerResponseRequest,
  type OperationKind,
  type StaffingDateSnapshot,
  type StaffingReceipt,
} from "./staffing";
import type { RosterImportDraft } from "./staffing-csv";
import type { ExceptionDraft, StaffingController } from "./staffing-state";

type ManagerResponseDraft<T extends ManagerResponseRequest = ManagerResponseRequest> =
  T extends ManagerResponseRequest
    ? Omit<T, "schema" | "request_id" | "operator" | "expected_revisions">
    : never;

export interface StaffingActions {
  refresh(): Promise<void>;
  importRoster(draft: RosterImportDraft): Promise<StaffingReceipt>;
  recordException(draft: ExceptionDraft): Promise<StaffingReceipt>;
  cancelException(exceptionId: string, note: string | null): Promise<StaffingReceipt>;
  correctException(
    exceptionId: string,
    replacement: ExceptionReplacementInput,
  ): Promise<StaffingReceipt>;
  generateSuggestion(): Promise<StaffingReceipt>;
  acceptSuggestion(
    suggestionId: string,
    candidateIndex: 1 | 2,
    note: string | null,
  ): Promise<StaffingReceipt>;
  modifySuggestion(
    suggestionId: string,
    candidateIndex: 1 | 2,
    operations: ManagerPatchOperation[],
    note: string | null,
  ): Promise<StaffingReceipt>;
  rejectSuggestion(
    suggestionId: string,
    reasonCode: "MANUAL_HANDLING" | "INSUFFICIENT_CONTEXT" | "OTHER",
    note: string | null,
  ): Promise<StaffingReceipt>;
  recover(): Promise<StaffingReceipt>;
  retryBusy(): Promise<StaffingReceipt>;
  retryUnknownGeneration(): Promise<StaffingReceipt>;
  acknowledgeWrite(): void;
}

export function createStaffingActions(
  controller: StaffingController,
  /** Pass a stable getter over the live value; do not capture an initial render's label. */
  getManagerLabel: () => string,
  requestIdFactory: (kind: OperationKind) => string = newStaffingRequestId,
): StaffingActions {
  const context = (): { snapshot: StaffingDateSnapshot; manager: string } => {
    const view = controller.view();
    if (view.snapshot === null) throw new Error("The staffing snapshot has not loaded yet.");
    if (view.read.status !== "ready" || view.read.stale) {
      throw new Error("Refresh the stale staffing snapshot before making a change.");
    }
    const manager = getManagerLabel();
    if (manager.trim() === "") throw new Error("A nonblank manager label is required.");
    return { snapshot: view.snapshot, manager };
  };

  const managerResponse = async (
    suggestionId: string,
    body: ManagerResponseDraft,
  ): Promise<StaffingReceipt> => {
    const { snapshot, manager } = context();
    const requestId = requestIdFactory("manager-response");
    const common = {
      schema: "nxt-staffing-manager-response/v1" as const,
      request_id: requestId,
      operator: manager,
      expected_revisions: structuredClone(snapshot.revisions),
    };
    let request: ManagerResponseRequest;
    switch (body.kind) {
      case "ACCEPT":
        request = { ...common, ...structuredClone(body) };
        break;
      case "MODIFY":
        request = { ...common, ...structuredClone(body) };
        break;
      case "REJECT":
        request = { ...common, ...structuredClone(body) };
        break;
    }
    return controller.submit({
      operationKind: "manager-response",
      targetId: suggestionId,
      body: request,
    });
  };

  return {
    refresh: () => controller.refresh(),
    async importRoster(draft) {
      const { snapshot, manager } = context();
      const requestId = requestIdFactory("roster-import");
      return controller.submit({
        operationKind: "roster-import",
        body: {
          schema: "nxt-staffing-roster-import/v1",
          request_id: requestId,
          operator: manager,
          expected_roster_revision: snapshot.revisions.roster,
          site_id: snapshot.context.site_id,
          deployment_id: snapshot.context.deployment_id,
          site_timezone: draft.site_timezone,
          effective_from_local_date: draft.effective_from_local_date,
          effective_until_local_date: draft.effective_until_local_date,
          source_ref: draft.source_ref,
          workers: structuredClone(draft.workers),
          availability: structuredClone(draft.availability),
          assignment_rules: structuredClone(draft.assignment_rules),
          regular_assignments: structuredClone(draft.regular_assignments),
          coverage: structuredClone(draft.coverage),
        },
      });
    },
    async recordException(draft) {
      const { snapshot, manager } = context();
      const common = {
        schema: "nxt-staffing-exception/v1" as const,
        operator: manager,
        service_date: snapshot.service_date,
        expected_roster_revision: snapshot.revisions.roster,
        expected_exception_set_revision: snapshot.revisions.exception_set,
        staff_id: draft.staff_id,
        note: draft.note,
      };
      let body: ExceptionRecordRequest;
      if (draft.kind === "LATE" || draft.kind === "EARLY_DEPARTURE") {
        if (typeof draft.time_local !== "string") {
          throw new Error("The exception time does not match its exception kind.");
        }
        body = {
          ...common,
          request_id: requestIdFactory("exception-record"),
          kind: draft.kind,
          time_local: draft.time_local,
        };
      } else {
        if (draft.time_local !== null) {
          throw new Error("The exception time does not match its exception kind.");
        }
        body = {
          ...common,
          request_id: requestIdFactory("exception-record"),
          kind: draft.kind,
          time_local: null,
        };
      }
      return controller.submit({ operationKind: "exception-record", body });
    },
    async cancelException(exceptionId, note) {
      const { snapshot, manager } = context();
      const requestId = requestIdFactory("exception-cancel");
      return controller.submit({
        operationKind: "exception-cancel",
        targetId: exceptionId,
        body: {
          schema: "nxt-staffing-exception-cancel/v1",
          request_id: requestId,
          operator: manager,
          expected_exception_set_revision: snapshot.revisions.exception_set,
          note,
        },
      });
    },
    async correctException(exceptionId, replacement) {
      const { snapshot, manager } = context();
      const requestId = requestIdFactory("exception-correct");
      const body: ExceptionCorrectRequest = {
        schema: "nxt-staffing-exception-correct/v1",
        request_id: requestId,
        operator: manager,
        expected_exception_set_revision: snapshot.revisions.exception_set,
        replacement: structuredClone(replacement),
      };
      return controller.submit({
        operationKind: "exception-correct",
        targetId: exceptionId,
        body,
      });
    },
    async generateSuggestion() {
      const { snapshot, manager } = context();
      const requestId = requestIdFactory("suggestion-generate");
      return controller.submit({
        operationKind: "suggestion-generate",
        body: {
          schema: "nxt-staffing-suggestion-generate/v1",
          request_id: requestId,
          operator: manager,
          service_date: snapshot.service_date,
          expected_revisions: structuredClone(snapshot.revisions),
          retry_of: null,
        },
      });
    },
    acceptSuggestion: (suggestionId, candidateIndex, note) =>
      managerResponse(suggestionId, {
        kind: "ACCEPT",
        candidate_index: candidateIndex,
        edited_operations: null,
        reason_code: "APPROVED",
        note,
      }),
    modifySuggestion: (suggestionId, candidateIndex, operations, note) =>
      managerResponse(suggestionId, {
        kind: "MODIFY",
        candidate_index: candidateIndex,
        edited_operations: structuredClone(operations),
        reason_code: "APPROVED_WITH_CHANGES",
        note,
      }),
    rejectSuggestion: (suggestionId, reasonCode, note) =>
      managerResponse(suggestionId, {
        kind: "REJECT",
        candidate_index: null,
        edited_operations: null,
        reason_code: reasonCode,
        note,
      }),
    recover: () => controller.recover(),
    retryBusy: () => controller.retryBusy(),
    retryUnknownGeneration: () => controller.retryUnknownGeneration(),
    acknowledgeWrite: () => controller.acknowledgeWrite(),
  };
}
