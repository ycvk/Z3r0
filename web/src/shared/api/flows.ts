import { apiBlob, defineJsonEndpoint } from "./client";
import { buildQuery } from "./query";
import type {
  CancelFlowResponse,
  CreateFlowRequest,
  CreateFlowResponse,
  FlowPathParams,
  GetFlowResponse,
  QueryFlowsParams,
  QueryFlowsResponse,
  SubmitFlowInputRequest,
  SubmitFlowInputResponse,
} from "./types";

const FLOWS_PATH = "/api/flows";

export const queryFlowsEndpoint = defineJsonEndpoint<[params: QueryFlowsParams], QueryFlowsResponse>(
  "GET", (params) => `${FLOWS_PATH}${buildQuery(params)}`,
);

export function queryFlows(params: { page: number; size: number; keyword?: string }) {
  return queryFlowsEndpoint({ page: params.page, size: params.size });
}

export const createFlow = defineJsonEndpoint<[payload: CreateFlowRequest], CreateFlowResponse>(
  "POST", () => FLOWS_PATH, (payload) => payload,
);

export const getFlow = defineJsonEndpoint<[publicId: FlowPathParams["public_id"]], GetFlowResponse>(
  "GET", (publicId) => `${FLOWS_PATH}/${encodeURIComponent(publicId)}`,
);

export const cancelFlow = defineJsonEndpoint<[publicId: FlowPathParams["public_id"]], CancelFlowResponse>(
  "POST", (publicId) => `${FLOWS_PATH}/${encodeURIComponent(publicId)}/cancel`,
);

export const submitFlowInput = defineJsonEndpoint<
  [publicId: FlowPathParams["public_id"], payload: SubmitFlowInputRequest], SubmitFlowInputResponse
>("POST", (publicId) => `${FLOWS_PATH}/${encodeURIComponent(publicId)}/input`, (_, payload) => payload);

export function downloadFlowReport(publicId: FlowPathParams["public_id"]) {
  return apiBlob(`${FLOWS_PATH}/${encodeURIComponent(publicId)}/report`);
}
