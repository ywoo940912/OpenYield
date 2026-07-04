import type {
  Panel, YieldEstimate, SpatialYield, CriticalArea,
  LotLineage, DefectDistribution, CNNStatus, IngestResult,
  ProductSpec, MonteCarloResult, LearningCurveResult,
  ParetoResult, SpcResult, TrendResult, DefectListResult,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  panels: {
    list: ()         => get<{ results: Panel[]; total: number }>("/panels"),
    get:  (id: string) => get<Panel>(`/panels/${id}`),
  },

  yield: {
    estimate:    (id: string) => get<YieldEstimate>(`/yield/${id}`),
    spatial:     (id: string) => get<SpatialYield>(`/yield/${id}/spatial`),
    criticalArea:(id: string) => get<CriticalArea>(`/yield/${id}/critical-area`),
  },

  genealogy: {
    lineage: (lotId: string) => get<LotLineage>(`/genealogy/${lotId}/lineage`),
    cycles:  ()              => get<string[]>("/genealogy/cycles"),
  },

  classify: {
    defects:   (panelId: string) => get<DefectDistribution>(`/classify/${panelId}/defects`),
    cnnStatus: ()                => get<CNNStatus>("/classify/cnn/status"),
  },

  ingest: {
    klarf2: async (file: File): Promise<IngestResult> => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${API_BASE}/ingest/klarf2`, { method: "POST", body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? res.statusText);
      }
      return res.json() as Promise<IngestResult>;
    },
  },

  products: {
    list:   ()                  => get<ProductSpec[]>("/products/specs"),
    get:    (id: string)        => get<ProductSpec>(`/products/specs/${id}`),
    create: async (body: Omit<ProductSpec, "die_area_mm2" | "created_at" | "updated_at">): Promise<ProductSpec> => {
      const res = await fetch(`${API_BASE}/products/specs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? res.statusText);
      }
      return res.json() as Promise<ProductSpec>;
    },
    update: async (id: string, patch: Partial<ProductSpec>): Promise<ProductSpec> => {
      const res = await fetch(`${API_BASE}/products/specs/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? res.statusText);
      }
      return res.json() as Promise<ProductSpec>;
    },
    delete: async (id: string): Promise<void> => {
      const res = await fetch(`${API_BASE}/products/specs/${id}`, { method: "DELETE" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? res.statusText);
      }
    },
  },

  analytics: {
    pareto: (panelId?: string, substrateType?: string) => {
      const p = new URLSearchParams();
      if (panelId) p.set("panel_id", panelId);
      if (substrateType) p.set("substrate_type", substrateType);
      return get<ParetoResult>(`/pareto?${p}`);
    },
    spc: (lotId?: string, substrateType?: string) => {
      const p = new URLSearchParams();
      if (lotId) p.set("lot_id", lotId);
      if (substrateType) p.set("substrate_type", substrateType);
      return get<SpcResult>(`/spc?${p}`);
    },
    trend: (substrateType?: string, limit = 50) => {
      const p = new URLSearchParams({ limit: String(limit) });
      if (substrateType) p.set("substrate_type", substrateType);
      return get<TrendResult>(`/trends?${p}`);
    },
  },

  defects: {
    list: (panelId?: string, sourceSystem?: string, limit = 500) => {
      const p = new URLSearchParams({ limit: String(limit) });
      if (panelId) p.set("panel_id", panelId);
      if (sourceSystem) p.set("source_system", sourceSystem);
      return get<DefectListResult>(`/defects?${p}`);
    },
  },

  simulate: {
    monteCarlo: async (params: {
      d0: number; die_area_mm2: number; wafer_diameter_mm?: number;
      n_runs?: number; critical_area_fraction?: number; alpha?: number;
    }): Promise<MonteCarloResult> => {
      const res = await fetch(`${API_BASE}/simulate/monte-carlo`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? res.statusText);
      }
      return res.json() as Promise<MonteCarloResult>;
    },
    monteCarloFromSpec: async (specId: string, nRuns = 2000): Promise<MonteCarloResult> => {
      const res = await fetch(`${API_BASE}/simulate/monte-carlo/spec/${specId}?n_runs=${nRuns}`, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? res.statusText);
      }
      return res.json() as Promise<MonteCarloResult>;
    },
    learningCurve: async (params: {
      current_yield: number; target_yield: number; model?: string;
      improvement_rate?: number; y_max?: number; n_months?: number;
      die_area_mm2?: number; initial_d0?: number;
    }): Promise<LearningCurveResult> => {
      const res = await fetch(`${API_BASE}/simulate/learning-curve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? res.statusText);
      }
      return res.json() as Promise<LearningCurveResult>;
    },
  },
};
