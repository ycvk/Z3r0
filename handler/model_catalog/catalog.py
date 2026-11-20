from schema.common.responses import CommonResponse
from service.model_catalog.refresh import get_model_catalog, refresh_model_catalog


async def get_model_catalog_handler() -> CommonResponse:
    return CommonResponse(data=get_model_catalog())


async def refresh_model_catalog_handler() -> CommonResponse:
    return CommonResponse(data=await refresh_model_catalog())
