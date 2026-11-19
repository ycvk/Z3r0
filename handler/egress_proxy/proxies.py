from schema.common.responses import CommonResponse
from schema.egress_proxy.proxies import (
    CreateEgressProxyRequest,
    DeleteEgressProxyResponse,
    EgressProxySchema,
    QueryEgressProxiesResponse,
    UpdateEgressProxyRequest,
)
from service.common.pagination import paginated_payload
from service.egress_proxy.proxies import (
    create_egress_proxy,
    delete_egress_proxy,
    query_egress_proxies,
    test_egress_proxy,
    update_egress_proxy,
)


async def create_egress_proxy_handler(request: CreateEgressProxyRequest) -> CommonResponse:
    proxy = await create_egress_proxy(
        proxy_type=request.proxy_type,
        proxy_host=request.proxy_host,
        proxy_port=request.proxy_port,
        proxy_account=request.proxy_account,
        proxy_password=request.proxy_password,
    )
    return CommonResponse(data=EgressProxySchema.model_validate(proxy))


async def update_egress_proxy_handler(id: int, request: UpdateEgressProxyRequest) -> CommonResponse:
    proxy = await update_egress_proxy(
        id=id,
        proxy_type=request.proxy_type,
        proxy_host=request.proxy_host,
        proxy_port=request.proxy_port,
        proxy_account=request.proxy_account,
        proxy_password=request.proxy_password,
    )
    return CommonResponse(data=EgressProxySchema.model_validate(proxy))


async def delete_egress_proxy_handler(id: int) -> CommonResponse:
    await delete_egress_proxy(id)
    return CommonResponse(data=DeleteEgressProxyResponse(id=id))


async def test_egress_proxy_handler(id: int) -> CommonResponse:
    return CommonResponse(data=await test_egress_proxy(id))


async def query_egress_proxies_handler(page: int, size: int, keyword: str) -> CommonResponse:
    proxies = await query_egress_proxies(page=page, size=size, keyword=keyword)
    return CommonResponse(data=QueryEgressProxiesResponse(
        **paginated_payload(
            proxies,
            [EgressProxySchema.model_validate(proxy) for proxy in proxies.items],
        ),
    ))
