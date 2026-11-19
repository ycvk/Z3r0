import { Button, Input, Popconfirm, Select, Tag, TextArea, Toast } from "../../shared/ui/semi";
import {
  ArrowLeftRight,
  Copy,
  Eye,
  EyeOff,
  FileCheck2,
  KeyRound,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { Fragment, useCallback, useEffect, useState } from "react";
import { showApiError, showApiSuccess } from "../../shared/api/feedback";
import { clearTraffic, getTrafficEntry, queryTraffic } from "../../shared/api/flows";
import {
  createTrafficEvidence,
  createWorkProjectCredential,
  deleteWorkProjectCredential,
  queryWorkProjectAssets,
  queryWorkProjectCredentials,
  queryWorkProjectWorkItems,
} from "../../shared/api/workProjects";
import type {
  SessionCredential,
  TrafficEntryDetail,
  TrafficEntrySummary,
  WorkProjectAsset,
  WorkProjectWorkItemRecord,
} from "../../shared/api/types";
import { AsyncContent } from "../../shared/components/AsyncContent";
import { FormField } from "../../shared/components/FormField";
import { ResourceModal } from "../../shared/components/ResourceModal";
import { ResourcePager, ResourceSearchForm } from "../../shared/components/ResourcePageShell";
import { useMountedRef } from "../../shared/hooks/useMountedRef";
import { usePagedResourceList } from "../../shared/hooks/usePagedResourceList";
import { useResourceSubmit } from "../../shared/hooks/useResourceSubmit";
import { formatDateTime } from "../../shared/lib/date";
import { WORK_PROJECT_WORK_ITEM_STATUS_LABEL } from "../../shared/lib/labels";
import { formatBytes } from "../../shared/lib/number";
import { UI_TEXT } from "../../shared/lib/uiText";
import { formatWorkProjectAsset } from "./workProjectView";

const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] as const;
const STATUS_BUCKETS = ["2xx", "3xx", "4xx", "5xx"] as const;
const CREDENTIAL_TYPES = [
  { label: "Password", value: "password" },
  { label: "Token", value: "token" },
  { label: "Cookie", value: "cookie" },
  { label: "API key", value: "api_key" },
  { label: "SSH key", value: "ssh_key" },
  { label: "Flag", value: "flag" },
  { label: "Other", value: "other" },
];

function statusTagColor(code: number): "green" | "blue" | "orange" | "red" | "grey" {
  if (code >= 200 && code < 300) return "green";
  if (code >= 300 && code < 400) return "blue";
  if (code >= 400 && code < 500) return "orange";
  if (code >= 500 && code < 600) return "red";
  return "grey";
}

function formatTrafficPayload(detail: TrafficEntryDetail): string {
  const reqHeaders = Object.keys(detail.request_headers ?? {}).length
    ? JSON.stringify(detail.request_headers, null, 2)
    : "(none)";
  const respHeaders = Object.keys(detail.response_headers ?? {}).length
    ? JSON.stringify(detail.response_headers, null, 2)
    : "(none)";
  return [
    "--- Request headers ---",
    reqHeaders,
    "--- Request body ---",
    detail.request_body || "(empty)",
    "--- Response headers ---",
    respHeaders,
    "--- Response body ---",
    detail.response_body || "(empty)",
  ].join("\n");
}

export function ProjectTrafficTab({
  projectId,
  enabled = true,
  onEvidenceCreated,
}: {
  projectId: number;
  enabled?: boolean;
  onEvidenceCreated?: () => void;
}) {
  const [method, setMethod] = useState<string>();
  const [statusBucket, setStatusBucket] = useState<string>();
  const [pathFilter, setPathFilter] = useState("");
  const [expandedDetail, setExpandedDetail] = useState<TrafficEntryDetail | null>(null);
  const [inspectingId, setInspectingId] = useState<number | null>(null);
  const [evidenceTarget, setEvidenceTarget] = useState<TrafficEntrySummary | null>(null);
  const { saving: clearing, submit: runClear } = useResourceSubmit();

  const traffic = usePagedResourceList<TrafficEntrySummary>({
    enabled,
    query: useCallback(
      ({ page, size, keyword }) =>
        queryTraffic({
          page,
          size,
          project_id: projectId,
          query: keyword || undefined,
          path: pathFilter.trim() || undefined,
          method: method || undefined,
          status: statusBucket || undefined,
        }),
      [method, pathFilter, projectId, statusBucket],
    ),
  });

  const handleToggleInspect = async (entry: TrafficEntrySummary) => {
    if (expandedDetail?.id === entry.id) {
      setExpandedDetail(null);
      return;
    }
    setInspectingId(entry.id);
    try {
      const response = await getTrafficEntry(entry.id);
      if (response.data) setExpandedDetail(response.data);
    } catch (error) {
      showApiError(error);
    } finally {
      setInspectingId(null);
    }
  };

  const handleClear = async () => {
    await runClear(async () => {
      const response = await clearTraffic({ project_id: projectId });
      setExpandedDetail(null);
      await traffic.loadItems();
      return response;
    });
  };

  return (
    <div className="project-record-paged has-filters">
      <div className="project-record-filters">
        <ResourceSearchForm
          value={traffic.keyword}
          placeholder="Search URL, headers, or body"
          onChange={traffic.setKeyword}
          onSearch={traffic.search}
        />
        <Input
          value={pathFilter}
          placeholder="Path filter"
          showClear
          onChange={(next) => {
            traffic.goToFirstPage();
            setPathFilter(next);
          }}
        />
        <Select
          value={method}
          placeholder="All methods"
          optionList={HTTP_METHODS.map((m) => ({ label: m, value: m }))}
          showClear
          onClear={() => {
            traffic.goToFirstPage();
            setMethod(undefined);
          }}
          onChange={(next) => {
            traffic.goToFirstPage();
            setMethod(typeof next === "string" && next ? next : undefined);
          }}
        />
        <Select
          value={statusBucket}
          placeholder="All statuses"
          optionList={STATUS_BUCKETS.map((s) => ({ label: s, value: s }))}
          showClear
          onClear={() => {
            traffic.goToFirstPage();
            setStatusBucket(undefined);
          }}
          onChange={(next) => {
            traffic.goToFirstPage();
            setStatusBucket(typeof next === "string" && next ? next : undefined);
          }}
        />
        <Button
          icon={<RefreshCw size={14} />}
          type="tertiary"
          loading={traffic.loading}
          onClick={() => void traffic.loadItems()}
        >
          Refresh
        </Button>
        <Popconfirm
          title="Clear captured traffic"
          content="Delete captured HTTP/HTTPS exchanges for this project?"
          okType="danger"
          cancelText={UI_TEXT.cancel}
          onConfirm={() => void handleClear()}
        >
          <Button
            icon={<Trash2 size={14} />}
            type="danger"
            theme="light"
            loading={clearing}
            disabled={traffic.total === 0}
          >
            Clear
          </Button>
        </Popconfirm>
      </div>

      <AsyncContent
        loading={traffic.loading}
        empty={traffic.loaded && !traffic.items.length}
        emptyIcon={<ArrowLeftRight size={42} />}
        emptyTitle="No captured HTTP/HTTPS traffic."
        fill
      >
        <div className="project-record-list">
          {traffic.items.map((entry) => {
            const isExpanded = expandedDetail?.id === entry.id;
            return (
              <article key={entry.id} className="project-record-item">
                <header>
                  <div>
                    <Tag>{entry.method}</Tag>
                    <Tag color={statusTagColor(entry.status_code)}>{entry.status_code || "-"}</Tag>
                    <strong>{entry.url}</strong>
                  </div>
                  <div className="project-record-actions">
                    <Button
                      size="small"
                      type="tertiary"
                      loading={inspectingId === entry.id}
                      onClick={() => void handleToggleInspect(entry)}
                    >
                      {isExpanded ? "Hide exchange" : "Inspect"}
                    </Button>
                    <Button
                      size="small"
                      theme="light"
                      type="primary"
                      icon={<FileCheck2 size={13} />}
                      onClick={() => setEvidenceTarget(entry)}
                    >
                      Save as evidence
                    </Button>
                  </div>
                </header>
                {isExpanded && expandedDetail ? (
                  <pre className="project-traffic-payload">{formatTrafficPayload(expandedDetail)}</pre>
                ) : null}
                <footer>
                  <span>{entry.source}</span>
                  <span>{formatBytes(entry.response_bytes)}</span>
                  <span>{entry.duration_ms} ms</span>
                  <span>{formatDateTime(entry.created_at)}</span>
                </footer>
              </article>
            );
          })}
        </div>
      </AsyncContent>
      <ResourcePager state={traffic} />

      <SaveTrafficEvidenceModal
        projectId={projectId}
        entry={evidenceTarget}
        onClose={() => setEvidenceTarget(null)}
        onSaved={() => {
          setEvidenceTarget(null);
          onEvidenceCreated?.();
        }}
      />
    </div>
  );
}

function SaveTrafficEvidenceModal({
  projectId,
  entry,
  onClose,
  onSaved,
}: {
  projectId: number;
  entry: TrafficEntrySummary | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const mountedRef = useMountedRef();
  const [workItems, setWorkItems] = useState<WorkProjectWorkItemRecord[]>([]);
  const [assets, setAssets] = useState<WorkProjectAsset[]>([]);
  const [optionsLoading, setOptionsLoading] = useState(false);
  const [workItemId, setWorkItemId] = useState<number>();
  const [primaryAssetId, setPrimaryAssetId] = useState<number>();
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const { saving, submit } = useResourceSubmit();

  useEffect(() => {
    if (!entry) return;
    const defaultTitle = `${entry.method} ${entry.host}${entry.path} [${entry.status_code}]`.slice(0, 255);
    setTitle(defaultTitle);
    setNote("");
    setWorkItemId(undefined);
    setPrimaryAssetId(undefined);

    let canceled = false;
    setOptionsLoading(true);
    Promise.all([
      queryWorkProjectWorkItems(projectId, { page: 1, size: 100, keyword: "" }),
      queryWorkProjectAssets(projectId, { page: 1, size: 100, keyword: "" }),
    ])
      .then(([wiRes, assetRes]) => {
        if (canceled || !mountedRef.current) return;
        const eligibleWorkItems = (wiRes.data?.items ?? []).filter(
          (item) => item.work_item.status !== "completed" && item.work_item.status !== "canceled",
        );
        const loadedAssets = assetRes.data?.items ?? [];
        setWorkItems(eligibleWorkItems);
        setAssets(loadedAssets);
        if (eligibleWorkItems.length > 0) {
          setWorkItemId(eligibleWorkItems[0].work_item.id);
        }
        const matchedAsset = loadedAssets.find((asset) => {
          const locator = asset.locator.trim().toLowerCase();
          return locator && (entry.url.toLowerCase().includes(locator) || entry.host.toLowerCase() === locator);
        });
        if (matchedAsset) {
          setPrimaryAssetId(matchedAsset.id);
        }
      })
      .catch((error) => {
        if (!canceled && mountedRef.current) showApiError(error);
      })
      .finally(() => {
        if (!canceled && mountedRef.current) setOptionsLoading(false);
      });
    return () => {
      canceled = true;
    };
  }, [entry, mountedRef, projectId]);

  const handleSubmit = async () => {
    if (!entry || !workItemId) return;
    await submit(async () => {
      const response = await createTrafficEvidence(projectId, entry.id, {
        work_item_id: workItemId,
        primary_asset_id: primaryAssetId ?? null,
        title: title.trim(),
        note: note.trim(),
      });
      showApiSuccess(response);
      onSaved();
      return response;
    });
  };

  return (
    <ResourceModal
      open={Boolean(entry)}
      title="Save HTTP exchange as evidence"
      titleIcon={<FileCheck2 size={17} />}
      saving={saving}
      submitLabel="Save evidence"
      submitDisabled={optionsLoading || !workItemId || !title.trim()}
      onCancel={onClose}
      onSubmit={handleSubmit}
    >
      <FormField label="Work item">
        <Select
          loading={optionsLoading}
          value={workItemId}
          placeholder="Select a target work item"
          optionList={workItems.map(({ work_item: item }) => ({
            value: item.id,
            label: `#${item.id} · ${item.title} (${WORK_PROJECT_WORK_ITEM_STATUS_LABEL[item.status]})`,
          }))}
          onChange={(next) => setWorkItemId(typeof next === "number" ? next : undefined)}
        />
        {!optionsLoading && workItems.length === 0 ? (
          <small>Create or reopen a non-terminal work item in this project first.</small>
        ) : null}
      </FormField>
      <FormField label="Primary asset (optional)">
        <Select
          loading={optionsLoading}
          value={primaryAssetId}
          placeholder="Link to an asset in project scope"
          showClear
          onClear={() => setPrimaryAssetId(undefined)}
          optionList={assets.map((asset) => ({
            value: asset.id,
            label: `${formatWorkProjectAsset(asset)} (${asset.locator})`,
          }))}
          onChange={(next) => setPrimaryAssetId(typeof next === "number" ? next : undefined)}
        />
      </FormField>
      <FormField label="Evidence title">
        <Input maxLength={255} value={title} onChange={setTitle} />
      </FormField>
      <FormField label="Analyst note (optional)">
        <TextArea rows={3} maxLength={2000} value={note} onChange={setNote} placeholder="Explain what this HTTP exchange demonstrates" />
      </FormField>
    </ResourceModal>
  );
}

export function ProjectCredentialsTab({
  projectId,
  enabled = true,
}: {
  projectId: number;
  enabled?: boolean;
}) {
  const [revealedIds, setRevealedIds] = useState<Set<number>>(() => new Set());
  const [createOpen, setCreateOpen] = useState(false);
  const { saving: deleting, submit: runDelete } = useResourceSubmit();

  const credentials = usePagedResourceList<SessionCredential>({
    enabled,
    query: useCallback(
      (params) => queryWorkProjectCredentials(projectId, params),
      [projectId],
    ),
  });

  const toggleReveal = (id: number) => {
    setRevealedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const copySecret = async (secret: string) => {
    try {
      await navigator.clipboard.writeText(secret);
      Toast.success("Secret copied to clipboard");
    } catch {
      Toast.error("Clipboard access denied");
    }
  };

  const handleDelete = async (credentialId: number) => {
    await runDelete(async () => {
      const response = await deleteWorkProjectCredential(projectId, credentialId);
      showApiSuccess(response);
      await credentials.loadItems();
      return response;
    });
  };

  return (
    <div className="project-record-paged has-filters">
      <div className="project-record-filters">
        <ResourceSearchForm
          value={credentials.keyword}
          placeholder="Search target, username, type, or note"
          onChange={credentials.setKeyword}
          onSearch={credentials.search}
        />
        <Button
          theme="solid"
          type="primary"
          icon={<Plus size={15} />}
          onClick={() => setCreateOpen(true)}
        >
          Add credential
        </Button>
      </div>

      <AsyncContent
        loading={credentials.loading}
        empty={credentials.loaded && !credentials.items.length}
        emptyIcon={<KeyRound size={42} />}
        emptyTitle="No credentials in project vault."
        fill
      >
        <div className="project-record-list">
          {credentials.items.map((cred) => {
            const revealed = revealedIds.has(cred.id);
            return (
              <article key={cred.id} className="project-record-item">
                <header>
                  <div>
                    <small>{cred.credential_type}</small>
                    <strong>{cred.target}</strong>
                  </div>
                  <div className="project-record-actions">
                    <Button
                      size="small"
                      theme="borderless"
                      type="tertiary"
                      icon={revealed ? <EyeOff size={14} /> : <Eye size={14} />}
                      aria-label={revealed ? "Hide secret" : "Reveal secret"}
                      onClick={() => toggleReveal(cred.id)}
                    />
                    <Button
                      size="small"
                      theme="borderless"
                      type="tertiary"
                      icon={<Copy size={14} />}
                      aria-label="Copy secret"
                      onClick={() => void copySecret(cred.secret)}
                    />
                    <Popconfirm
                      title="Delete credential"
                      content={`Remove credential for ${cred.target}?`}
                      okType="danger"
                      cancelText={UI_TEXT.cancel}
                      onConfirm={() => void handleDelete(cred.id)}
                    >
                      <Button
                        size="small"
                        theme="borderless"
                        type="danger"
                        loading={deleting}
                        icon={<Trash2 size={14} />}
                        aria-label={`Delete credential for ${cred.target}`}
                      />
                    </Popconfirm>
                  </div>
                </header>
                <dl>
                  {cred.username ? (
                    <Fragment>
                      <dt>Username</dt>
                      <dd>{cred.username}</dd>
                    </Fragment>
                  ) : null}
                  <dt>Secret</dt>
                  <dd>
                    <code>{revealed ? cred.secret : "••••••••••••"}</code>
                  </dd>
                  {cred.note ? (
                    <Fragment>
                      <dt>Note</dt>
                      <dd>{cred.note}</dd>
                    </Fragment>
                  ) : null}
                </dl>
                <footer>
                  <span>{cred.project_id ? "Project vault" : "Session-captured"}</span>
                  <span>{formatDateTime(cred.updated_at)}</span>
                </footer>
              </article>
            );
          })}
        </div>
      </AsyncContent>
      <ResourcePager state={credentials} />

      <AddProjectCredentialModal
        projectId={projectId}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false);
          void credentials.loadItems();
        }}
      />
    </div>
  );
}

function AddProjectCredentialModal({
  projectId,
  open,
  onClose,
  onCreated,
}: {
  projectId: number;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [target, setTarget] = useState("");
  const [credentialType, setCredentialType] = useState("password");
  const [username, setUsername] = useState("");
  const [secret, setSecret] = useState("");
  const [note, setNote] = useState("");
  const { saving, submit } = useResourceSubmit();

  useEffect(() => {
    if (!open) return;
    setTarget("");
    setCredentialType("password");
    setUsername("");
    setSecret("");
    setNote("");
  }, [open]);

  const handleSave = async () => {
    if (!target.trim() || !secret.trim()) return;
    await submit(async () => {
      const response = await createWorkProjectCredential(projectId, {
        target: target.trim(),
        credential_type: credentialType,
        username: username.trim(),
        secret: secret.trim(),
        note: note.trim(),
      });
      showApiSuccess(response);
      onCreated();
      return response;
    });
  };

  return (
    <ResourceModal
      open={open}
      title="Add project credential"
      titleIcon={<KeyRound size={17} />}
      saving={saving}
      submitLabel={UI_TEXT.save}
      submitDisabled={!target.trim() || !secret.trim()}
      onCancel={onClose}
      onSubmit={handleSave}
    >
      <FormField label="Target URL / host">
        <Input autoFocus maxLength={255} placeholder="https://portal.target.local/login" value={target} onChange={setTarget} />
      </FormField>
      <FormField label="Credential type">
        <Select
          value={credentialType}
          optionList={CREDENTIAL_TYPES}
          onChange={(next) => setCredentialType(String(next ?? "password"))}
        />
      </FormField>
      <FormField label="Username (optional)">
        <Input maxLength={255} value={username} onChange={setUsername} />
      </FormField>
      <FormField label="Secret / token">
        <Input mode="password" maxLength={4096} value={secret} onChange={setSecret} />
      </FormField>
      <FormField label="Note (optional)">
        <TextArea rows={2} maxLength={500} value={note} onChange={setNote} placeholder="Privilege level or provenance" />
      </FormField>
    </ResourceModal>
  );
}
