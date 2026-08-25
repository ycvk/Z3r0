import { Tag } from "@douyinfe/semi-ui";
import { Waypoints } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FLOW_STATUS } from "../../shared/api/generated/constants";
import { createFlow, queryFlows } from "../../shared/api/flows";
import type { CreateFlowRequest, Flow, FlowSummary } from "../../shared/api/types";
import { AsyncContent } from "../../shared/components/AsyncContent";
import { ResourcePageShell } from "../../shared/components/ResourcePageShell";
import { ResourceTable, type ResourceColumn } from "../../shared/components/ResourceTable";
import { ResourceIdentity } from "../../shared/components/ResourceCells";
import { useAdminResourceHeader } from "../../shared/hooks/useAdminResourceHeader";
import { usePagedResourceList } from "../../shared/hooks/usePagedResourceList";
import { useResourceSubmit } from "../../shared/hooks/useResourceSubmit";
import { formatDateTime } from "../../shared/lib/date";
import { FlowCreateModal } from "./FlowCreateModal";
import { FLOW_STATUS_COLOR, FLOW_STATUS_LABEL } from "./flowView";

export function FlowsPage() {
  const flows = usePagedResourceList<FlowSummary>({ query: queryFlows });
  const [modalOpen, setModalOpen] = useState(false);
  const navigate = useNavigate();

  useAdminResourceHeader({
    createLabel: "Create Flow",
    refreshLabel: "Refresh flows",
    loading: flows.loading,
    onCreate: () => setModalOpen(true),
    onRefresh: flows.loadItems,
  });

  const { saving, submit } = useResourceSubmit({
    onSuccess: async (response) => {
      setModalOpen(false);
      const publicId = (response.data as Flow | null | undefined)?.public_id;
      await flows.loadItems();
      if (publicId) navigate(`/flows/${publicId}`);
    },
  });

  const summary = useMemo(() => ({
    active: flows.items.filter((flow) => (
      flow.status === FLOW_STATUS.QUEUED
      || flow.status === FLOW_STATUS.PLANNING
      || flow.status === FLOW_STATUS.RUNNING
      || flow.status === FLOW_STATUS.WAITING
      || flow.status === FLOW_STATUS.REPORTING
    )).length,
    completed: flows.items.filter((flow) => flow.status === FLOW_STATUS.COMPLETED).length,
  }), [flows.items]);

  const columns: ResourceColumn<FlowSummary>[] = [
    {
      key: "title", header: "Flow", width: "minmax(0, 1.4fr)",
      render: (flow) => (
        <button className="resource-link-button" type="button" onClick={() => navigate(`/flows/${flow.public_id}`)}>
          <ResourceIdentity icon={<Waypoints size={18} />} title={flow.title || "Untitled flow"} detail={flow.public_id} />
        </button>
      ),
    },
    {
      key: "status", header: "Status", width: "140px",
      render: (flow) => <Tag color={FLOW_STATUS_COLOR[flow.status]}>{FLOW_STATUS_LABEL[flow.status]}</Tag>,
    },
    {
      key: "sandbox", header: "Sandbox", width: "110px",
      render: (flow) => flow.sandbox_container_id,
    },
    {
      key: "project", header: "Project", width: "110px",
      render: (flow) => flow.project_id ?? "—",
    },
    {
      key: "updated", header: "Updated", width: "minmax(0, 0.8fr)",
      render: (flow) => formatDateTime(flow.updated_at),
    },
  ];

  return (
    <div className="resource-page">
      <ResourcePageShell
        searchPlaceholder="Search is not required for flows"
        state={flows}
        metrics={[
          { label: "In flight", value: summary.active },
          { label: "Completed", value: summary.completed },
          { label: "Total", value: flows.total },
        ]}
        empty={flows.loaded && flows.items.length === 0}
        emptyIcon={<Waypoints size={28} />}
        emptyTitle="No flows yet"
      >
        <AsyncContent loading={flows.loading} empty={flows.loaded && flows.items.length === 0}>
          <ResourceTable ariaLabel="Flows" columns={columns} rows={flows.items} rowKey={(flow) => flow.public_id} />
        </AsyncContent>
      </ResourcePageShell>
      <FlowCreateModal
        open={modalOpen}
        saving={saving}
        onCancel={() => setModalOpen(false)}
        onSubmit={(payload: CreateFlowRequest) => submit(() => createFlow(payload))}
      />
    </div>
  );
}
