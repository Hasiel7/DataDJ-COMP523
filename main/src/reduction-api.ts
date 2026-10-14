export type Algorithm = 'umap' | 'tsne' | 'phate';
export type Preprocessing = 'none' | 'standardize';
export type JsonScalar = string | number | boolean | null;

export interface ColumnMetadata {
    name: string;
    dtype: string;
    non_missing_count: number;
    unique_count: number;
}

export interface DatasetMetadata {
    dataset_id: string;
    filename: string;
    created_at: string;
    source_row_count: number;
    observation_count: number;
    feature_count: number;
    columns: ColumnMetadata[];
    feature_columns: string[];
    label_column: string | null;
    identifier_columns: string[];
    row_id_column: string | null;
    preprocessing: Preprocessing;
    random_seed: number;
    subsampled: boolean;
}

export interface UMAPConfig {
    algorithm: 'umap';
    n_components: 2 | 3;
    n_neighbors: number;
    min_dist: number;
    n_epochs: number;
}

export interface TSNEConfig {
    algorithm: 'tsne';
    n_components: 2 | 3;
    perplexity: number;
    learning_rate: number;
    max_iter: number;
}

export interface PHATEConfig {
    algorithm: 'phate';
    n_components: 2 | 3;
    knn: number;
    t: number;
    decay: number;
}

export type AlgorithmConfig = UMAPConfig | TSNEConfig | PHATEConfig;

export interface EmbeddingRequest {
    dataset_id: string;
    config: AlgorithmConfig;
    random_seed: number;
}

export interface EmbeddingCsvDownload {
    blob: Blob;
    embeddingId: string;
    filename: string;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api';

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, init);
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
        const detail = payload && typeof payload === 'object' && 'detail' in payload
            ? (payload as { detail: unknown }).detail
            : `Request failed (${response.status})`;
        const message = typeof detail === 'string' ? detail : JSON.stringify(detail);
        throw new Error(message);
    }
    return payload as T;
}

export function checkHealth(): Promise<{ status: 'ok' }> {
    return requestJson('/health');
}

export function uploadDataset(
    file: File,
    options: {
        labelColumn: string | null;
        rowIdColumn: string | null;
        identifierColumns: string[];
        preprocessing: Preprocessing;
        maxSamples: number | null;
        randomSeed: number;
    },
): Promise<DatasetMetadata> {
    const body = new FormData();
    body.append('file', file);
    if (options.labelColumn) body.append('label_column', options.labelColumn);
    if (options.rowIdColumn) body.append('row_id_column', options.rowIdColumn);
    for (const column of options.identifierColumns) {
        body.append('identifier_columns', column);
    }
    body.append('preprocessing', options.preprocessing);
    if (options.maxSamples !== null) body.append('max_samples', String(options.maxSamples));
    body.append('random_seed', String(options.randomSeed));
    return requestJson('/datasets', { method: 'POST', body });
}

export async function createEmbedding(request: EmbeddingRequest): Promise<EmbeddingCsvDownload> {
    const response = await fetch(`${API_BASE}/embeddings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
    });
    if (!response.ok) {
        const payload: unknown = await response.json().catch(() => null);
        const detail = payload && typeof payload === 'object' && 'detail' in payload
            ? (payload as { detail: unknown }).detail
            : `Request failed (${response.status})`;
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }

    const disposition = response.headers.get('Content-Disposition');
    const filename = disposition?.match(/filename="?([^";]+)"?/i)?.[1] ?? 'embedding.csv';
    return {
        blob: await response.blob(),
        embeddingId: response.headers.get('X-Embedding-ID') ?? '',
        filename,
    };
}