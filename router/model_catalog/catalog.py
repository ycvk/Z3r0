from fastapi import APIRouter, Depends

from handler.model_catalog.catalog import get_model_catalog_handler, refresh_model_catalog_handler
from middleware.system_user import require_admin
from router.common.responses import BAD_GATEWAY_RESPONSE, COMMON_ERROR_RESPONSES
from schema.common.responses import CommonResponse
from schema.model_catalog.catalog import ModelCatalog


ADMIN_ONLY = [Depends(require_admin)]

router = APIRouter(prefix="/model-catalog", tags=["model-catalog"])

router.add_api_route(
    "",
    get_model_catalog_handler,
    methods=["GET"],
    dependencies=ADMIN_ONLY,
    response_model=CommonResponse[ModelCatalog],
    responses=COMMON_ERROR_RESPONSES,
)

router.add_api_route(
    "/refresh",
    refresh_model_catalog_handler,
    methods=["POST"],
    dependencies=ADMIN_ONLY,
    response_model=CommonResponse[ModelCatalog],
    responses={**COMMON_ERROR_RESPONSES, **BAD_GATEWAY_RESPONSE},
)
