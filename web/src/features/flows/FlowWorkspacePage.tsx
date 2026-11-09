import { Button, Tag, TextArea } from "../../shared/ui/semi";
import { ArrowLeft, Download, SquareStop, Waypoints } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AGENT_EVENT_TYPE, FLOW_STATUS } from "../../shared/api/generated/constants";
import { cancelFlow, downloadFlowReport, getFlow, submitFlowInput } from "../../shared/api/flows";
import { listAgentEvents } from "../../shared/api/agentSessions";
import { showApiError, showApiSuccess } from "../../shared/api/feedback";
import type { AgentContentEvent, Flow } from "../../shared/api/types";
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

  const loadFlow = useCallback(async () => {
    if (!flowId) return null;
    const response = await getFlow(flowId);
    return response.data ?? null;
  }, [flowId]);

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
    } catch (error) {
      if (mountedRef.current && !silent) showApiError(error);
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [loadFlow, mountedRef]);

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
            </section>
            <aside className="flow-workspace-side">
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
              <p className="flow-workspace-meta">Updated {formatDateTime(flow.updated_at)}</p>
            </aside>
          </div>
        ) : null}
      </AsyncContent>
    </div>
  );
}
