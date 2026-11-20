import { defineJsonEndpoint } from "./client";
import type { GetModelCatalogResponse, RefreshModelCatalogResponse } from "./types";

const MODEL_CATALOG_PATH = "/api/model-catalog";

export const getModelCatalog = defineJsonEndpoint<[], GetModelCatalogResponse>(
  "GET", () => MODEL_CATALOG_PATH,
);
export const refreshModelCatalog = defineJsonEndpoint<[], RefreshModelCatalogResponse>(
  "POST", () => `${MODEL_CATALOG_PATH}/refresh`,
);
