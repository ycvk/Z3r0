import { TextArea } from "../../shared/ui/semi";
import { Waypoints } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { SANDBOX_CONTAINER_STATUS } from "../../shared/api/generated/constants";
import { queryAvailableSandboxContainers } from "../../shared/api/sandboxContainers";
import { queryWorkProjects } from "../../shared/api/workProjects";
import type { CreateFlowRequest, SandboxContainer, WorkProjectSummary } from "../../shared/api/types";
import { FormField } from "../../shared/components/FormField";
import { OptionListSelect } from "../../shared/components/OptionListSelect";
import { ResourceModal } from "../../shared/components/ResourceModal";
import { useOptionList } from "../../shared/hooks/useOptionList";

type Props = {
  open: boolean;
  saving: boolean;
  onCancel: () => void;
  onSubmit: (payload: CreateFlowRequest) => Promise<void>;
};

type Values = {
  goal: string;
  sandbox_container_id: number | null;
  project_id: number | null;
};

const emptyValues = (): Values => ({
  goal: "",
  sandbox_container_id: null,
  project_id: null,
});

export function FlowCreateModal({ open, saving, onCancel, onSubmit }: Props) {
  const [values, setValues] = useState<Values>(emptyValues);
  const loadContainers = useCallback(
    (params: { page: number; size: number; keyword: string }) => queryAvailableSandboxContainers(params),
    [],
  );
  const containers = useOptionList<SandboxContainer>({ enabled: open, query: loadContainers });
  const projects = useOptionList<WorkProjectSummary>({ enabled: open, query: queryWorkProjects });

  useEffect(() => {
    if (open) setValues(emptyValues());
  }, [open]);

  const runningContainers = containers.items.filter((item) => item.status === SANDBOX_CONTAINER_STATUS.RUNNING);
  const containerOptions = useMemo(
    () => runningContainers.map((container) => ({
      value: container.id,
      label: `${container.container_name} · ${container.image_name}`,
    })),
    [runningContainers],
  );
  const projectOptions = useMemo(
    () => projects.items.map((project) => ({ value: project.id, label: project.name })),
    [projects.items],
  );
  const canSubmit = values.goal.trim().length > 0 && values.sandbox_container_id != null;

  return (
    <ResourceModal
      open={open}
      title="Create Flow"
      titleIcon={<Waypoints size={18} />}
      saving={saving}
      submitLabel="Start Flow"
      submitDisabled={!canSubmit}
      onCancel={onCancel}
      onSubmit={() => {
        if (values.sandbox_container_id == null) return;
        return onSubmit({
          goal: values.goal.trim(),
          sandbox_container_id: values.sandbox_container_id,
          project_id: values.project_id,
        });
      }}
    >
      <FormField label="Goal">
        <TextArea
          value={values.goal}
          rows={6}
          placeholder="Authorized objective, scope, stop conditions"
          onChange={(goal) => setValues((current) => ({ ...current, goal }))}
        />
      </FormField>
      <FormField label="Sandbox">
        <OptionListSelect
          filter
          style={{ width: "100%" }}
          placeholder={containers.busy ? "Loading sandbox containers" : "Select a running sandbox"}
          emptyContent="No running sandbox containers"
          source={containers}
          optionList={containerOptions}
          value={values.sandbox_container_id ?? undefined}
          onChange={(value) => setValues((current) => ({
            ...current,
            sandbox_container_id: typeof value === "number" ? value : null,
          }))}
        />
      </FormField>
      <FormField label="Work project (optional)">
        <OptionListSelect
          filter
          showClear
          style={{ width: "100%" }}
          placeholder="None"
          emptyContent="No work projects"
          source={projects}
          optionList={projectOptions}
          value={values.project_id ?? undefined}
          onChange={(value) => setValues((current) => ({
            ...current,
            project_id: typeof value === "number" ? value : null,
          }))}
        />
      </FormField>
    </ResourceModal>
  );
}
