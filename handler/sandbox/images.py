from schema.common.responses import CommonResponse
from schema.sandbox.images import (
    CreateSandboxImageRequest,
    DeleteSandboxImageResponse,
    QuerySandboxImagesResponse,
    SandboxImageSchema,
)
from service.common.pagination import paginated_payload
from service.sandbox.images import (
    create_sandbox_image,
    delete_sandbox_image,
    query_sandbox_images,
)


async def create_sandbox_image_handler(request: CreateSandboxImageRequest) -> CommonResponse:
    sandbox_image = await create_sandbox_image(
        image_name=request.image_name,
        control_proxy_port=request.control_proxy_port,
        supports_tor=request.supports_tor,
    )
    return CommonResponse(
        message="Sandbox image created",
        data=SandboxImageSchema.model_validate(sandbox_image),
    )


async def delete_sandbox_image_handler(id: int) -> CommonResponse:
    await delete_sandbox_image(id)
    return CommonResponse(data=DeleteSandboxImageResponse(id=id))


async def query_sandbox_images_handler(page: int, size: int, keyword: str) -> CommonResponse:
    sandbox_images = await query_sandbox_images(page=page, size=size, keyword=keyword)
    return CommonResponse(data=QuerySandboxImagesResponse(
        **paginated_payload(
            sandbox_images,
            [SandboxImageSchema.model_validate(image) for image in sandbox_images.items],
        ),
    ))
