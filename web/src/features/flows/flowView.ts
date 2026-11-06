import { FLOW_STATUS, FLOW_SUBTASK_STATUS } from "../../shared/api/generated/constants";
import type { FlowStatus, FlowSubtaskStatus } from "../../shared/api/types";

export const FLOW_STATUS_LABEL: Record<FlowStatus, string> = {
  [FLOW_STATUS.QUEUED]: "Queued",
  [FLOW_STATUS.PLANNING]: "Planning",
  [FLOW_STATUS.RUNNING]: "Running",
  [FLOW_STATUS.WAITING]: "Waiting",
  [FLOW_STATUS.REPORTING]: "Reporting",
  [FLOW_STATUS.COMPLETED]: "Completed",
  [FLOW_STATUS.FAILED]: "Failed",
  [FLOW_STATUS.CANCELLED]: "Cancelled",
};

export const FLOW_STATUS_COLOR: Record<FlowStatus, "grey" | "blue" | "green" | "orange" | "red" | "teal"> = {
  [FLOW_STATUS.QUEUED]: "grey",
  [FLOW_STATUS.PLANNING]: "blue",
  [FLOW_STATUS.RUNNING]: "teal",
  [FLOW_STATUS.WAITING]: "orange",
  [FLOW_STATUS.REPORTING]: "blue",
  [FLOW_STATUS.COMPLETED]: "green",
  [FLOW_STATUS.FAILED]: "red",
  [FLOW_STATUS.CANCELLED]: "grey",
};

export const FLOW_SUBTASK_LABEL: Record<FlowSubtaskStatus, string> = {
  [FLOW_SUBTASK_STATUS.PLANNED]: "Planned",
  [FLOW_SUBTASK_STATUS.RUNNING]: "Running",
  [FLOW_SUBTASK_STATUS.DONE]: "Done",
  [FLOW_SUBTASK_STATUS.SKIPPED]: "Skipped",
  [FLOW_SUBTASK_STATUS.FAILED]: "Failed",
};

export function isFlowTerminal(status: FlowStatus): boolean {
  return status === FLOW_STATUS.COMPLETED
    || status === FLOW_STATUS.FAILED
    || status === FLOW_STATUS.CANCELLED;
}
