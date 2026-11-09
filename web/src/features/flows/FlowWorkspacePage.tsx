import { Button, Input, Select, Tag, TextArea } from "../../shared/ui/semi";
import { ArrowLeft, Download, KeyRound, Plus, RefreshCw, SquareStop, Trash2, Waypoints } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AGENT_EVENT_TYPE, FLOW_STATUS } from "../../shared/api/generated/constants";
import {
  addFlowCredential,
  cancelFlow,
  clearTraffic,
  downloadFlowReport,
  getFlow,
  getTrafficEntry,
  queryTraffic,
  removeFlowCredential,
  submitFlowInput,
} from "../../shared/api/flows";
import { listAgentEvents } from "../../shared/api/agentSessions";
import { showApiError, showApiSuccess } from "../../shared/api/feedback";
import type { AgentContentEvent, Flow, TrafficEntryDetail, TrafficEntrySummary } from "../../shared/api/types";
import { AsyncContent } from "../../shared/components/AsyncContent";
import { useAdminHeaderActions } from "../../app/layouts/AdminLayout";
import { useMountedRef } from "../../shared/hooks/useMountedRef";
import { formatDateTime } from "../../shared/lib/date";
import { saveBlob } from "../../shared/lib/download";
import { FLOW_STATUS_COLOR, FLOW_STATUS_LABEL, FLOW_SUBTASK_LABEL, isFlowTerminal } from "./flowView";

function eventText(event: AgentContentEvent): string {
  if ("display_text" in event && typeof event.display_text === "string" && event.display_text.trim()) {
    return event.display_text;
  }
  if ("text" in event && typeof event.text === "string" && event.text.trim()) {
    return event.text;
  }
  if ("message" in event && typeof event.message === "string" && event.message.trim()) {
    return event.message;
  }
  if ("name" in event && typeof event.name === "string") {
    return event.name;
  }
  return event.type;
}

export function FlowWorkspacePage() {
  const { flowId = "" } = useParams();
  const navigate = useNavigate();
  const mountedRef = useMountedRef();
  const setHeaderActions = useAdminHeaderActions();
  const [flow, setFlow] = useState<Flow | null>(null);
  const [events, setEvents] = useState<AgentContentEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [askText, setAskText] = useState("");
  const [credTarget, setCredTarget] = useState("");
  const [credUser, setCredUser] = useState("");
  const [credSecret, setCredSecret] = useState("");
  const [credType, setCredType] = useState("password");
  const [trafficItems, setTrafficItems] = useState<TrafficEntrySummary[]>([]);
  const [trafficQuery, setTrafficQuery] = useState("");
  const [trafficStatus, setTrafficStatus] = useState("");
  const [selectedTraffic, setSelectedTraffic] = useState<TrafficEntryDetail | null>(null);

  const loadFlow = useCallback(async () => {
    if (!flowId) return null;
    const response = await getFlow(flowId);
    return response.data ?? null;
  }, [flowId]);

  const loadTraffic = useCallback(async (containerId: number, projectId?: number | null) => {
    try {
      const res = await queryTraffic({
        page: 1,
        size: 30,
        sandbox_container_id: containerId,
        project_id: projectId ?? undefined,
        query: trafficQuery.trim() || undefined,
        status: trafficStatus || undefined,
      });
      if (mountedRef.current) setTrafficItems(res.data?.items ?? []);
    } catch {
      // Traffic polling is best-effort alongside flow status.
    }
  }, [mountedRef, trafficQuery, trafficStatus]);

  const refresh = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const next = await loadFlow();
      if (!mountedRef.current) return;
      setFlow(next);
      if (next?.session_id) {
        const history = await listAgentEvents(next.session_id, { limit: 200 });
        if (!mountedRef.current) return;
        setEvents(history.data?.items ?? []);
      }
      if (next?.sandbox_container_id) {
        await loadTraffic(next.sandbox_container_id, next.project_id);
      }
    } catch (error) {
      if (mountedRef.current && !silent) showApiError(error);
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [loadFlow, loadTraffic, mountedRef]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!flow || isFlowTerminal(flow.status)) return undefined;
    const timer = window.setInterval(() => {
      void refresh({ silent: true });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [flow, refresh]);

  useEffect(() => {
    if (!flow) {
      setHeaderActions(null);
      return;
    }
    setHeaderActions(
      <>
        {flow.report_id ? (
          <Button
            icon={<Download size={16} />}
            onClick={async () => {
              try {
                const { blob, filename } = await downloadFlowReport(flow.public_id);
                saveBlob(blob, filename || `${flow.public_id}.md`);
              } catch (error) {
                showApiError(error);
              }
            }}
          >
            Report
          </Button>
        ) : null}
        {!isFlowTerminal(flow.status) ? (
          <Button
            type="danger"
            icon={<SquareStop size={16} />}
            loading={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const response = await cancelFlow(flow.public_id);
                showApiSuccess(response);
                if (response.data) setFlow(response.data);
              } catch (error) {
                showApiError(error);
              } finally {
                setBusy(false);
              }
            }}
          >
            Cancel
          </Button>
        ) : null}
      </>,
    );
    return () => setHeaderActions(null);
  }, [busy, flow, setHeaderActions]);

  const submitAsk = async () => {
    if (!flow || !askText.trim()) return;
    setBusy(true);
    try {
      const response = await submitFlowInput(flow.public_id, { text: askText.trim() });
      showApiSuccess(response);
      setAskText("");
      if (response.data) setFlow(response.data);
      await refresh({ silent: true });
    } catch (error) {
      showApiError(error);
    } finally {
      setBusy(false);
    }
  };

  const handleAddCredential = async () => {
    if (!flow || !credTarget.trim() || !credSecret.trim()) return;
    setBusy(true);
    try {
      const res = await addFlowCredential(flow.public_id, {
        target: credTarget.trim(),
        username: credUser.trim(),
        secret: credSecret.trim(),
        credential_type: credType,
        note: "",
      });
      showApiSuccess(res);
      setCredTarget("");
      setCredUser("");
      setCredSecret("");
      if (res.data) setFlow(res.data);
    } catch (error) {
      showApiError(error);
    } finally {
      setBusy(false);
    }
  };

  const handleRemoveCredential = async (credentialId: number) => {
    if (!flow) return;
    try {
      const res = await removeFlowCredential(flow.public_id, credentialId);
      if (res.data) setFlow(res.data);
    } catch (error) {
      showApiError(error);
    }
  };

  const handleInspectTraffic = async (id: number) => {
    if (selectedTraffic?.id === id) {
      setSelectedTraffic(null);
      return;
    }
    try {
      const res = await getTrafficEntry(id);
      if (res.data) setSelectedTraffic(res.data);
    } catch (error) {
      showApiError(error);
    }
  };

  const handleClearTraffic = async () => {
    if (!flow) return;
    try {
      await clearTraffic({
        sandbox_container_id: flow.sandbox_container_id,
        project_id: flow.project_id ?? undefined,
      });
      setTrafficItems([]);
      setSelectedTraffic(null);
    } catch (error) {
      showApiError(error);
    }
  };

  return (
    <div className="flow-workspace">
      <div className="flow-workspace-header">
        <Button theme="borderless" icon={<ArrowLeft size={16} />} onClick={() => navigate("/flows")}>
          Flows
        </Button>
        <div className="flow-workspace-title">
          <Waypoints size={18} />
          <strong>{flow?.title || "Flow"}</strong>
          {flow ? <Tag color={FLOW_STATUS_COLOR[flow.status]}>{FLOW_STATUS_LABEL[flow.status]}</Tag> : null}
        </div>
      </div>
      <AsyncContent loading={loading} empty={!loading && flow == null} emptyTitle="Flow not found" emptyIcon={<Waypoints size={28} />}>
        {flow ? (
          <div className="flow-workspace-grid">
            <section className="flow-workspace-main">
              <h3>Goal</h3>
              <pre className="flow-workspace-goal">{flow.goal}</pre>
              {flow.status === FLOW_STATUS.WAITING ? (
                <div className="flow-workspace-ask">
                  <h3>Operator input required</h3>
                  <p>{flow.ask_question || "The Flow is waiting for an answer."}</p>
                  <TextArea rows={4} value={askText} onChange={setAskText} />
                  <Button theme="solid" disabled={!askText.trim()} loading={busy} onClick={() => void submitAsk()}>
                    Submit answer
                  </Button>
                </div>
              ) : null}
              {flow.error ? <p className="flow-workspace-error">{flow.error}</p> : null}
              <h3>Transcript</h3>
              <div className="flow-workspace-transcript">
                {events.length === 0 ? <p>Waiting for controller.</p> : events.map((event, index) => (
                  <article key={`${event.type}-${index}`} className="flow-workspace-event">
                    <span>{event.type === AGENT_EVENT_TYPE.USER_MESSAGE ? "Operator / controller" : event.type}</span>
                    <pre>{eventText(event)}</pre>
                  </article>
                ))}
              </div>
              <h3>Captured HTTP Traffic ({trafficItems.length})</h3>
              <div style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
                <Input
                  placeholder="Search URL, headers, or body..."
                  value={trafficQuery}
                  onChange={setTrafficQuery}
                  style={{ width: 240 }}
                />
                <Select
                  value={trafficStatus}
                  onChange={(val) => setTrafficStatus(String(val ?? ""))}
                  style={{ width: 120 }}
                  optionList={[
                    { label: "All status", value: "" },
                    { label: "2xx", value: "2xx" },
                    { label: "3xx", value: "3xx" },
                    { label: "4xx", value: "4xx" },
                    { label: "5xx", value: "5xx" },
                  ]}
                />
                <Button icon={<RefreshCw size={14} />} onClick={() => void loadTraffic(flow.sandbox_container_id, flow.project_id)}>
                  Filter
                </Button>
                <Button type="danger" icon={<Trash2 size={14} />} onClick={() => void handleClearTraffic()}>
                  Clear
                </Button>
              </div>
              {trafficItems.length === 0 ? <p>No HTTP traffic captured yet.</p> : (
                <div className="flow-workspace-transcript">
                  {trafficItems.map((item) => (
                    <article key={item.id} className="flow-workspace-event" style={{ cursor: "pointer" }} onClick={() => void handleInspectTraffic(item.id)}>
                      <span>
                        <strong>{item.method}</strong> [{item.status_code}] {item.url} ({item.response_bytes}B · {item.duration_ms}ms)
                      </span>
                      {selectedTraffic?.id === item.id ? (
                        <pre>{`--- Request Headers ---\n${JSON.stringify(selectedTraffic.request_headers, null, 2)}\n--- Request Body ---\n${selectedTraffic.request_body || "(empty)"}\n--- Response Headers ---\n${JSON.stringify(selectedTraffic.response_headers, null, 2)}\n--- Response Body ---\n${selectedTraffic.response_body || "(empty)"}`}</pre>
                      ) : null}
                    </article>
                  ))}
                </div>
              )}
            </section>
            <aside className="flow-workspace-side">
              <h3>Token Usage</h3>
              <p className="flow-workspace-meta">
                Total: <strong>{(flow.total_tokens ?? 0).toLocaleString()}</strong> tokens ({flow.llm_requests ?? 0} requests)
                <br />
                In: {(flow.input_tokens ?? 0).toLocaleString()} · Out: {(flow.output_tokens ?? 0).toLocaleString()} · Cached: {(flow.cached_tokens ?? 0).toLocaleString()}
              </p>
              <h3>Subtasks</h3>
              {(flow.subtasks ?? []).length === 0 ? <p>No subtasks yet.</p> : (
                <ol className="flow-workspace-subtasks">
                  {(flow.subtasks ?? []).map((item) => (
                    <li key={item.id}>
                      <strong>{item.title}</strong>
                      <span>{FLOW_SUBTASK_LABEL[item.status]}</span>
                    </li>
                  ))}
                </ol>
              )}
              <h3><KeyRound size={15} style={{ verticalAlign: "middle", marginRight: 4 }} />Credentials Vault ({(flow.credentials ?? []).length})</h3>
              {(flow.credentials ?? []).length === 0 ? <p>No credentials saved.</p> : (
                <ul className="flow-workspace-subtasks">
                  {(flow.credentials ?? []).map((cred) => (
                    <li key={cred.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                      <div>
                        <strong>[{cred.credential_type}] {cred.target}</strong>
                        <br />
                        <span>{cred.username ? `${cred.username} : ` : ""}{cred.secret}</span>
                      </div>
                      <Button theme="borderless" type="danger" icon={<Trash2 size={14} />} onClick={() => void handleRemoveCredential(cred.id)} />
                    </li>
                  ))}
                </ul>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 8 }}>
                <Input placeholder="Target (URL / host)" value={credTarget} onChange={setCredTarget} />
                <div style={{ display: "flex", gap: 6 }}>
                  <Input placeholder="Username" value={credUser} onChange={setCredUser} />
                  <Select
                    value={credType}
                    onChange={(val) => setCredType(String(val ?? "password"))}
                    style={{ width: 120 }}
                    optionList={[
                      { label: "password", value: "password" },
                      { label: "token", value: "token" },
                      { label: "cookie", value: "cookie" },
                      { label: "api_key", value: "api_key" },
                      { label: "flag", value: "flag" },
                    ]}
                  />
                </div>
                <Input placeholder="Secret / token / cookie" value={credSecret} onChange={setCredSecret} />
                <Button icon={<Plus size={14} />} disabled={!credTarget.trim() || !credSecret.trim()} loading={busy} onClick={() => void handleAddCredential()}>
                  Save Credential
                </Button>
              </div>
              <p className="flow-workspace-meta">Updated {formatDateTime(flow.updated_at)}</p>
            </aside>
          </div>
        ) : null}
      </AsyncContent>
    </div>
  );
}
