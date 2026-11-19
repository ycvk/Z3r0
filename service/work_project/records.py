"""Bounded, user-visible work project projections."""

from collections import Counter
import hashlib
import json

from sqlalchemy import String, cast, func, or_
from sqlmodel import select

from database import get_async_session
from model.agent.subordinates import AgentSubordinateTask
from model.sandbox.traffic import SandboxTrafficEntry
from model.work_project.assets import WorkProjectAsset
from model.work_project.evidence import (
    WorkProjectAttackPathStepEvidence,
    WorkProjectEvidence,
    WorkProjectFindingEvidence,
)
from model.work_project.findings import WorkProjectFinding, WorkProjectFindingAsset
from model.work_project.graph import WorkProjectAttackPath, WorkProjectAttackPathStep, WorkProjectRelation
from model.work_project.projects import WorkProject
from model.work_project.workflow import (
    WorkProjectWorkItem,
    WorkProjectWorkItemTarget,
    WorkProjectWorkLog,
)
from schema.agent.credentials import CreateSessionCredentialRequest, SessionCredentialSchema
from schema.agent.subordinates import AgentSubordinateStatus
from schema.system_user.users import SystemUserRole
from schema.work_project.assets import (
    WorkProjectAssetKind,
    WorkProjectAssetSchema,
    WorkProjectAssetScope,
)
from schema.work_project.evidence import (
    WorkProjectEvidenceKind,
    WorkProjectEvidenceRequest,
    WorkProjectEvidenceSchema,
    WorkProjectEvidenceStatus,
)
from schema.work_project.findings import (
    WorkProjectFindingAssetLinkSchema,
    WorkProjectFindingSchema,
    WorkProjectFindingSeverity,
    WorkProjectFindingVerification,
)
from schema.work_project.graph import (
    WorkProjectAssertionStatus,
    WorkProjectAttackPathSchema,
    WorkProjectAttackPathStepSchema,
    WorkProjectRelationSchema,
    derive_attack_path_status,
)
from schema.work_project.records import (
    CreateTrafficEvidenceRequest,
    WorkProjectAttackPathRecordSchema,
    WorkProjectEvidenceRecordSchema,
    WorkProjectFindingRecordSchema,
    WorkProjectGraphViewSchema,
    WorkProjectOverviewSchema,
)
from schema.work_project.workflow import (
    WorkProjectTargetStatus,
    WorkProjectWorkItemStatus,
    WorkProjectWorkLogSchema,
)
from service.agent import credentials as agent_credentials
from service.common.access import can_access_work_project
from service.common.pagination import Page, paginate_statement
from service.work_project.assets import query_work_project_assets
from service.work_project.evidence import (
    OPERATOR_EVIDENCE_ACTOR,
    create_work_project_evidence,
    query_work_project_evidence,
)
from service.work_project.work_item_records import query_work_project_work_item_records


WORK_PROJECT_GRAPH_MAX_NODES = 1_000
WORK_PROJECT_GRAPH_MAX_RELATIONS = 4_000


async def get_work_project_overview_for_user(
    project_id: int, *, user_id: int, user_role: SystemUserRole,
) -> WorkProjectOverviewSchema | None:
    if not await can_access_work_project(project_id, user_id, user_role):
        return None
    async with get_async_session() as session:
        assets = list((await session.exec(select(WorkProjectAsset).where(WorkProjectAsset.project_id == project_id))).all())
        targets = list((await session.exec(
            select(WorkProjectWorkItemTarget)
            .join(WorkProjectWorkItem, WorkProjectWorkItem.id == WorkProjectWorkItemTarget.work_item_id)
            .where(WorkProjectWorkItem.project_id == project_id)
        )).all())
        work_items = list((await session.exec(select(WorkProjectWorkItem).where(WorkProjectWorkItem.project_id == project_id))).all())
        findings = list((await session.exec(select(WorkProjectFinding).where(WorkProjectFinding.project_id == project_id))).all())
        evidence_total = int((await session.exec(
            select(func.count()).select_from(WorkProjectEvidence).where(WorkProjectEvidence.project_id == project_id)
        )).one())
        paths = list((await session.exec(select(WorkProjectAttackPath).where(WorkProjectAttackPath.project_id == project_id))).all())
        steps = list((await session.exec(select(WorkProjectAttackPathStep).where(WorkProjectAttackPathStep.project_id == project_id))).all())
        running_agents = int((await session.exec(
            select(func.count(func.distinct(AgentSubordinateTask.agent_code)))
            .join(WorkProjectWorkItem, WorkProjectWorkItem.id == AgentSubordinateTask.work_item_id)
            .where(
                WorkProjectWorkItem.project_id == project_id,
                AgentSubordinateTask.status == AgentSubordinateStatus.RUNNING,
            )
        )).one())
    touched = {target.asset_id for target in targets}
    steps_by_path: dict[int, list[WorkProjectAttackPathStepSchema]] = {}
    for step in steps:
        steps_by_path.setdefault(step.path_id, []).append(WorkProjectAttackPathStepSchema.model_validate(step))
    path_statuses = Counter(
        derive_attack_path_status(steps_by_path.get(path.id or 0, []), path.archived_at).value for path in paths
    )
    return WorkProjectOverviewSchema(
        asset_total=len(assets),
        in_scope_asset_total=sum(asset.scope == WorkProjectAssetScope.IN_SCOPE for asset in assets),
        untouched_asset_total=sum(asset.scope == WorkProjectAssetScope.IN_SCOPE and asset.id not in touched for asset in assets),
        covered_target_total=sum(target.status == WorkProjectTargetStatus.COVERED for target in targets),
        blocked_target_total=sum(target.status == WorkProjectTargetStatus.BLOCKED for target in targets),
        work_item_status_counts=dict(Counter(str(item.status) for item in work_items)),
        finding_verification_counts=dict(Counter(str(item.verification) for item in findings)),
        attack_path_status_counts=dict(path_statuses),
        evidence_total=evidence_total,
        running_agent_count=running_agents,
    )


async def query_work_project_assets_for_user(
    project_id: int,
    *,
    page: int,
    size: int,
    keyword: str,
    kind: WorkProjectAssetKind | None,
    scope: WorkProjectAssetScope | None,
    user_id: int,
    user_role: SystemUserRole,
):
    if not await can_access_work_project(project_id, user_id, user_role):
        return None
    return await query_work_project_assets(
        project_id, page=page, size=size, keyword=keyword, kind=kind, scope=scope
    )


async def query_work_project_evidence_for_user(
    project_id: int,
    *,
    page: int,
    size: int,
    keyword: str,
    kind: WorkProjectEvidenceKind | None,
    status: WorkProjectEvidenceStatus | None,
    user_id: int,
    user_role: SystemUserRole,
):
    if not await can_access_work_project(project_id, user_id, user_role):
        return None
    result = await query_work_project_evidence(
        project_id, page=page, size=size, keyword=keyword, kind=kind, status=status
    )
    asset_ids = {item.primary_asset_id for item in result.items if item.primary_asset_id is not None}
    async with get_async_session() as session:
        assets = {item.id: item for item in (await session.exec(select(WorkProjectAsset).where(WorkProjectAsset.id.in_(asset_ids)))).all()} if asset_ids else {}
    return Page(
        page=result.page, size=result.size, total=result.total,
        items=[WorkProjectEvidenceRecordSchema(
            evidence=item,
            primary_asset=WorkProjectAssetSchema.model_validate(assets[item.primary_asset_id]) if item.primary_asset_id in assets else None,
        ) for item in result.items],
    )


async def query_work_project_findings_for_user(
    project_id: int,
    *,
    page: int,
    size: int,
    keyword: str,
    verification: WorkProjectFindingVerification | None,
    severity: WorkProjectFindingSeverity | None,
    user_id: int,
    user_role: SystemUserRole,
):
    if not await can_access_work_project(project_id, user_id, user_role):
        return None
    statement = select(WorkProjectFinding).where(WorkProjectFinding.project_id == project_id).order_by(WorkProjectFinding.id.desc())
    if verification is not None:
        statement = statement.where(WorkProjectFinding.verification == verification)
    if severity is not None:
        statement = statement.where(WorkProjectFinding.severity == severity)
    if keyword := keyword.strip():
        pattern = f"%{keyword}%"
        statement = statement.where(or_(
            WorkProjectFinding.title.ilike(pattern),
            WorkProjectFinding.description.ilike(pattern),
            WorkProjectFinding.impact.ilike(pattern),
            WorkProjectFinding.cwe_id.ilike(pattern),
            cast(WorkProjectFinding.verification, String).ilike(pattern),
        ))
    result = await paginate_statement(statement, page=page, size=size)
    finding_ids = [item.id for item in result.items if item.id is not None]
    async with get_async_session() as session:
        links = list((await session.exec(select(WorkProjectFindingAsset).where(WorkProjectFindingAsset.finding_id.in_(finding_ids)))).all()) if finding_ids else []
        evidence_links = list((await session.exec(select(WorkProjectFindingEvidence).where(WorkProjectFindingEvidence.finding_id.in_(finding_ids)))).all()) if finding_ids else []
        asset_ids = {item.primary_asset_id for item in result.items} | {link.asset_id for link in links}
        evidence_ids = {link.evidence_id for link in evidence_links}
        assets = {item.id: item for item in (await session.exec(select(WorkProjectAsset).where(WorkProjectAsset.id.in_(asset_ids)))).all()}
        evidence = {item.id: item for item in (await session.exec(select(WorkProjectEvidence).where(WorkProjectEvidence.id.in_(evidence_ids)))).all()} if evidence_ids else {}
    links_by_finding: dict[int, list[WorkProjectFindingAsset]] = {}
    evidence_by_finding: dict[int, list[WorkProjectEvidence]] = {}
    for link in links:
        links_by_finding.setdefault(link.finding_id, []).append(link)
    for link in evidence_links:
        if link.evidence_id in evidence:
            evidence_by_finding.setdefault(link.finding_id, []).append(evidence[link.evidence_id])
    return Page(page=result.page, size=result.size, total=result.total, items=[
        WorkProjectFindingRecordSchema(
            finding=WorkProjectFindingSchema.model_validate(item),
            primary_asset=WorkProjectAssetSchema.model_validate(assets[item.primary_asset_id]),
            affected_assets=[WorkProjectAssetSchema.model_validate(assets[link.asset_id]) for link in links_by_finding.get(item.id or 0, [])],
            asset_links=[WorkProjectFindingAssetLinkSchema.model_validate(link) for link in links_by_finding.get(item.id or 0, [])],
            evidence=[WorkProjectEvidenceSchema.model_validate(value) for value in evidence_by_finding.get(item.id or 0, [])],
        ) for item in result.items
    ])


async def query_work_project_attack_paths_for_user(
    project_id: int,
    *,
    page: int,
    size: int,
    keyword: str,
    user_id: int,
    user_role: SystemUserRole,
):
    if not await can_access_work_project(project_id, user_id, user_role):
        return None
    statement = select(WorkProjectAttackPath).where(WorkProjectAttackPath.project_id == project_id)
    if keyword := keyword.strip():
        pattern = f"%{keyword}%"
        statement = statement.where(or_(
            WorkProjectAttackPath.title.ilike(pattern),
            WorkProjectAttackPath.objective.ilike(pattern),
            WorkProjectAttackPath.summary.ilike(pattern),
        ))
    result = await paginate_statement(statement.order_by(WorkProjectAttackPath.id.desc()), page=page, size=size)
    path_ids = [item.id for item in result.items if item.id is not None]
    async with get_async_session() as session:
        steps = list((await session.exec(select(WorkProjectAttackPathStep).where(WorkProjectAttackPathStep.path_id.in_(path_ids)).order_by(WorkProjectAttackPathStep.path_id, WorkProjectAttackPathStep.sequence))).all()) if path_ids else []
        step_ids = [item.id for item in steps if item.id is not None]
        evidence_links = list((await session.exec(select(WorkProjectAttackPathStepEvidence).where(WorkProjectAttackPathStepEvidence.step_id.in_(step_ids)))).all()) if step_ids else []
        asset_ids = {value for step in steps for value in (step.source_asset_id, step.target_asset_id)}
        evidence_ids = {link.evidence_id for link in evidence_links}
        assets = {item.id: item for item in (await session.exec(select(WorkProjectAsset).where(WorkProjectAsset.id.in_(asset_ids)))).all()} if asset_ids else {}
        evidence = {item.id: item for item in (await session.exec(select(WorkProjectEvidence).where(WorkProjectEvidence.id.in_(evidence_ids)))).all()} if evidence_ids else {}
    steps_by_path: dict[int, list[WorkProjectAttackPathStep]] = {}
    evidence_by_step: dict[int, list[WorkProjectEvidence]] = {}
    for step in steps:
        steps_by_path.setdefault(step.path_id, []).append(step)
    for link in evidence_links:
        if link.evidence_id in evidence:
            evidence_by_step.setdefault(link.step_id, []).append(evidence[link.evidence_id])
    items = []
    for path in result.items:
        path_steps = steps_by_path.get(path.id or 0, [])
        path_assets = {value for step in path_steps for value in (step.source_asset_id, step.target_asset_id)}
        items.append(WorkProjectAttackPathRecordSchema(
            path=WorkProjectAttackPathSchema.model_validate(path),
            status=derive_attack_path_status([WorkProjectAttackPathStepSchema.model_validate(step) for step in path_steps], path.archived_at),
            steps=[WorkProjectAttackPathStepSchema.model_validate(step) for step in path_steps],
            assets=[WorkProjectAssetSchema.model_validate(assets[item]) for item in sorted(path_assets) if item in assets],
            evidence=[
                WorkProjectEvidenceSchema.model_validate(item)
                for item in {
                    evidence.id: evidence
                    for step in path_steps
                    for evidence in evidence_by_step.get(step.id or 0, [])
                }.values()
            ],
        ))
    return Page(page=result.page, size=result.size, total=result.total, items=items)


async def query_work_project_work_items_for_user(
    project_id: int,
    *,
    page: int,
    size: int,
    keyword: str,
    status: WorkProjectWorkItemStatus | None,
    assignee_agent_code: str,
    user_id: int,
    user_role: SystemUserRole,
):
    if not await can_access_work_project(project_id, user_id, user_role):
        return None
    return await query_work_project_work_item_records(
        project_id,
        page=page,
        size=size,
        keyword=keyword,
        status=status,
        assignee_agent_code=assignee_agent_code,
    )


async def query_work_project_activity_for_user(project_id: int, *, page: int, size: int, user_id: int, user_role: SystemUserRole):
    if not await can_access_work_project(project_id, user_id, user_role):
        return None
    result = await paginate_statement(
        select(WorkProjectWorkLog).where(WorkProjectWorkLog.project_id == project_id).order_by(WorkProjectWorkLog.id.desc()),
        page=page, size=size,
    )
    return Page(page=result.page, size=result.size, total=result.total, items=[WorkProjectWorkLogSchema.model_validate(item) for item in result.items])


async def get_work_project_graph_for_user(project_id: int, *, user_id: int, user_role: SystemUserRole):
    if not await can_access_work_project(project_id, user_id, user_role):
        return None
    async with get_async_session() as session:
        assets = list((await session.exec(select(WorkProjectAsset).where(WorkProjectAsset.project_id == project_id).order_by(WorkProjectAsset.id).limit(WORK_PROJECT_GRAPH_MAX_NODES + 1))).all())
        visible = assets[:WORK_PROJECT_GRAPH_MAX_NODES]
        asset_ids = [item.id for item in visible if item.id is not None]
        asset_id_set = set(asset_ids)
        relations = list((await session.exec(select(WorkProjectRelation).where(
            WorkProjectRelation.project_id == project_id,
            WorkProjectRelation.status != WorkProjectAssertionStatus.REFUTED,
            WorkProjectRelation.source_asset_id.in_(asset_ids),
            WorkProjectRelation.target_asset_id.in_(asset_ids),
        ).order_by(WorkProjectRelation.id).limit(WORK_PROJECT_GRAPH_MAX_RELATIONS + 1))).all()) if asset_ids else []
        finding_primary_assets = list((await session.exec(select(
            WorkProjectFinding.id,
            WorkProjectFinding.primary_asset_id,
        ).where(WorkProjectFinding.project_id == project_id))).all())
        finding_affected_assets = list((await session.exec(select(
            WorkProjectFindingAsset.finding_id,
            WorkProjectFindingAsset.asset_id,
        ).join(
            WorkProjectFinding,
            WorkProjectFinding.id == WorkProjectFindingAsset.finding_id,
        ).where(WorkProjectFinding.project_id == project_id))).all())
        active_work = list((await session.exec(select(WorkProjectWorkItemTarget.asset_id, func.count()).join(WorkProjectWorkItem, WorkProjectWorkItem.id == WorkProjectWorkItemTarget.work_item_id).where(
            WorkProjectWorkItem.project_id == project_id,
            WorkProjectWorkItem.status.in_({WorkProjectWorkItemStatus.ACTIVE, WorkProjectWorkItemStatus.BLOCKED, WorkProjectWorkItemStatus.REVIEW}),
        ).group_by(WorkProjectWorkItemTarget.asset_id))).all())
        path_steps = list((await session.exec(select(WorkProjectAttackPathStep.source_asset_id, WorkProjectAttackPathStep.target_asset_id).where(WorkProjectAttackPathStep.project_id == project_id))).all())
    finding_counts = Counter(
        asset_id
        for _, asset_id in {
            *finding_primary_assets,
            *finding_affected_assets,
        }
        if asset_id in asset_id_set
    )
    path_counts = Counter(value for pair in path_steps for value in pair)
    return WorkProjectGraphViewSchema(
        assets=[WorkProjectAssetSchema.model_validate(item) for item in visible],
        relations=[WorkProjectRelationSchema.model_validate(item) for item in relations[:WORK_PROJECT_GRAPH_MAX_RELATIONS]],
        finding_counts=dict(finding_counts),
        active_work_item_counts={int(asset_id): int(count) for asset_id, count in active_work},
        attack_path_counts=dict(path_counts),
        is_truncated=len(assets) > WORK_PROJECT_GRAPH_MAX_NODES or len(relations) > WORK_PROJECT_GRAPH_MAX_RELATIONS,
    )


async def query_work_project_credentials_for_user(
    project_id: int,
    *,
    page: int,
    size: int,
    keyword: str,
    user_id: int,
    user_role: SystemUserRole,
) -> Page[SessionCredentialSchema] | None:
    if not await can_access_work_project(project_id, user_id, user_role):
        return None
    return await agent_credentials.query_project_credentials(
        project_id,
        page=page,
        size=size,
        keyword=keyword,
    )


async def create_work_project_credential_for_user(
    project_id: int,
    request: CreateSessionCredentialRequest,
    *,
    user_id: int,
    user_role: SystemUserRole,
) -> SessionCredentialSchema | None:
    if not await can_access_work_project(project_id, user_id, user_role):
        return None
    return await agent_credentials.upsert_credential(
        owner_id=user_id,
        session_id="",
        project_id=project_id,
        target=request.target,
        credential_type=request.credential_type,
        username=request.username,
        secret=request.secret,
        note=request.note,
    )


async def delete_work_project_credential_for_user(
    project_id: int,
    credential_id: int,
    *,
    user_id: int,
    user_role: SystemUserRole,
) -> bool | None:
    if not await can_access_work_project(project_id, user_id, user_role):
        return None
    return await agent_credentials.delete_credential(
        credential_id,
        project_id=project_id,
    )


def _format_traffic_evidence_summary(entry: SandboxTrafficEntry, note: str) -> str:
    sections: list[str] = []
    if note:
        sections.append(note)
    sections.append(
        f"**Exchange**: `{entry.method} {entry.url}` -> `{entry.status_code}` "
        f"({entry.response_bytes} bytes, {entry.duration_ms} ms)"
    )
    try:
        req_headers = json.loads(entry.request_headers_json or "{}")
    except Exception:
        req_headers = {}
    try:
        resp_headers = json.loads(entry.response_headers_json or "{}")
    except Exception:
        resp_headers = {}

    req_lines = [f"{entry.method} {entry.path or '/'} HTTP/1.1"]
    if entry.host:
        req_lines.append(f"Host: {entry.host}")
    if isinstance(req_headers, dict):
        for k, v in list(req_headers.items())[:25]:
            if k.lower() != "host":
                req_lines.append(f"{k}: {v}")
    if entry.request_body:
        req_lines.extend(("", entry.request_body[:1800]))
    sections.append("### Request\n```http\n" + "\n".join(req_lines) + "\n```")

    resp_lines = [f"HTTP/1.1 {entry.status_code}"]
    if isinstance(resp_headers, dict):
        for k, v in list(resp_headers.items())[:25]:
            resp_lines.append(f"{k}: {v}")
    if entry.response_body:
        resp_lines.extend(("", entry.response_body[:2400]))
    sections.append("### Response\n```http\n" + "\n".join(resp_lines) + "\n```")
    text = "\n\n".join(sections)
    return text[:7950] + "\n...[truncated]" if len(text) > 8000 else text


def _traffic_exchange_sha256(entry: SandboxTrafficEntry) -> str:
    digest = hashlib.sha256()
    for part in (
        entry.method,
        "\n",
        entry.url,
        "\n",
        str(entry.status_code),
        "\n",
        entry.request_headers_json or "",
        "\n",
        entry.request_body or "",
        "\n",
        entry.response_headers_json or "",
        "\n",
        entry.response_body or "",
    ):
        digest.update(part.encode("utf-8", errors="replace"))
    return digest.hexdigest()


async def create_work_project_evidence_from_traffic_for_user(
    project_id: int,
    traffic_id: int,
    request: CreateTrafficEvidenceRequest,
    *,
    user_id: int,
    user_role: SystemUserRole,
) -> tuple[WorkProjectEvidenceRecordSchema | None, str, bool]:
    """Create an HTTP_EXCHANGE evidence record from a captured traffic entry.

    Returns (record, error_message, not_found).
    """
    if not await can_access_work_project(project_id, user_id, user_role):
        return None, "Work project not found", True

    async with get_async_session() as session:
        project = await session.get(WorkProject, project_id)
        if project is None:
            return None, "Work project not found", True
        entry = await session.get(SandboxTrafficEntry, traffic_id)
        if entry is None:
            return None, "Traffic entry not found", True
        belongs_to_project = entry.project_id == project_id or (
            project.sandbox_container_id is not None
            and entry.sandbox_container_id == project.sandbox_container_id
        )
        if not belongs_to_project:
            return None, "Traffic entry not found", True

    default_title = f"{entry.method} {entry.host}{entry.path} [{entry.status_code}]".strip()
    title = (request.title or default_title or f"HTTP exchange #{traffic_id}")[:255]
    reference = f"traffic://{traffic_id} {entry.method} {entry.url}".strip()[:2000]
    summary = _format_traffic_evidence_summary(entry, request.note)

    ev_request = WorkProjectEvidenceRequest(
        kind=WorkProjectEvidenceKind.HTTP_EXCHANGE,
        title=title,
        summary=summary,
        reference=reference,
        sha256=_traffic_exchange_sha256(entry),
        primary_asset_id=request.primary_asset_id,
        work_item_id=request.work_item_id,
        captured_at=entry.created_at,
    )
    created, error = await create_work_project_evidence(
        project_id,
        ev_request,
        created_by_agent_code=OPERATOR_EVIDENCE_ACTOR,
        created_from_session_id="",
    )
    if created is None:
        return None, error or "Failed to create evidence", False

    primary_asset_schema: WorkProjectAssetSchema | None = None
    if created.primary_asset_id is not None:
        async with get_async_session() as session:
            asset = await session.get(WorkProjectAsset, created.primary_asset_id)
            if asset is not None:
                primary_asset_schema = WorkProjectAssetSchema.model_validate(asset)
    return WorkProjectEvidenceRecordSchema(evidence=created, primary_asset=primary_asset_schema), "", False
