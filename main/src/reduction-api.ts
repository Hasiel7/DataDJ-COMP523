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

export interface NumericRangeRequest {
    start: string;
    stop: string;
    step: string;
}

export interface GridJobRequest {
    dataset_id: string;
    algorithms: Partial<Record<Algorithm, Record<string, NumericRangeRequest>>>;
    n_components: 2 | 3;
    random_seed: number;
}

export interface JobSubmissionResponse {
    job_id: string;
    status: 'queued';
}

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

export interface JobSummary {
    job_id: string;
    dataset_id: string;
    status: JobStatus;
    artifact_path: string | null;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
}

export interface EmbeddingCsvDownload {
    blob: Blob;
    embeddingId: string;
    filename: string;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api';

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await authenticatedFetch(path, init);
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

export function submitJob(request: GridJobRequest): Promise<JobSubmissionResponse> {
    return requestJson('/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
    });
}

export function getJobStatus(jobId: string): Promise<JobSummary> {
    return requestJson(`/jobs/${encodeURIComponent(jobId)}`);
}

export function getJobBatch(jobId: string): Promise<BatchManifest> {
    return requestJson(`/jobs/${encodeURIComponent(jobId)}/batch`);
}

export function getJobRun(
    jobId: string, runNumber: number, signal?: AbortSignal,
): Promise<BatchRunResult> {
    return requestJson(`/jobs/${encodeURIComponent(jobId)}/runs/${runNumber}`, { signal });
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
        hasHeader: boolean;
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
    body.append('has_header', String(options.hasHeader));
    return requestJson('/datasets', { method: 'POST', body });
}

export async function createEmbedding(request: EmbeddingRequest): Promise<EmbeddingCsvDownload> {
    const response = await authenticatedFetch('/embeddings', {
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

export interface BatchRunRecord {
    run_number: number;
    config: AlgorithmConfig;
    observation_count: number;
    embedding_id?: string;
    execution_time_seconds?: number;
}

export interface BatchManifest {
    batch_id: string;
    dataset: DatasetMetadata;
    random_seed: number;
    n_components: 2 | 3;
    runs: BatchRunRecord[];
}

export interface BatchCatalog {
    batch_id: string;
    filename: string;
    observation_count: number;
    n_components: 2 | 3;
    runs: BatchRunRecord[];
}

export interface BatchRunResult {
    algorithm: Algorithm;
    hyperparameters: Record<string, number>;
    observation_count: number;
    output_dimensions: 2 | 3;
    row_ids: JsonScalar[];
    coordinates: number[][];
    labels: JsonScalar[] | null;
    embedding_id?: string;
    dataset_id?: string;
    random_seed?: number;
    execution_time_seconds?: number;
}

export function getBatchCatalog(batchId: string, signal?: AbortSignal): Promise<BatchCatalog> {
    return requestJson(`/batches/${encodeURIComponent(batchId)}`, { signal });
}

export function getBatchRun(
    batchId: string,
    runNumber: number,
    signal?: AbortSignal,
): Promise<BatchRunResult> {
    return requestJson(
        `/batches/${encodeURIComponent(batchId)}/runs/${runNumber}`,
        { signal },
    );
}

interface DirectImportStatus {
    import_id: string;
    status: 'uploading' | 'queued' | 'running' | 'succeeded' | 'failed';
    batch_id: string | null;
    error_message: string | null;
}

async function uploadDirectBatch(
    file: File,
    onProgress: (loaded: number, total: number) => void,
): Promise<BatchCatalog> {
    if (file.size < 1 || file.size > 2 * 1024 * 1024 * 1024) {
        throw new Error('Batch CSV must be nonempty and no larger than 2 GiB.');
    }
    const upload = await requestJson<{ import_id: string; upload_url: string }>('/batch-imports', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, size: file.size }),
    });
    await new Promise<void>((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open('PUT', upload.upload_url);
        request.setRequestHeader('x-ms-blob-type', 'BlockBlob');
        request.setRequestHeader('x-ms-version', '2021-12-02');
        request.upload.onprogress = (event) => {
            if (event.lengthComputable) onProgress(event.loaded, event.total);
        };
        request.onerror = () => reject(new Error('Network error while uploading to storage.'));
        request.onabort = () => reject(new DOMException('Upload cancelled.', 'AbortError'));
        request.onload = () => {
            if (request.status >= 200 && request.status < 300) resolve();
            else reject(new Error(`Storage upload failed (${request.status}).`));
        };
        request.send(file);
    });
    onProgress(file.size, file.size);
    let result = await requestJson<DirectImportStatus>(
        `/batch-imports/${encodeURIComponent(upload.import_id)}/complete`, { method: 'POST' },
    );
    while (result.status === 'queued' || result.status === 'running') {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        result = await requestJson<DirectImportStatus>(
            `/batch-imports/${encodeURIComponent(upload.import_id)}`,
        );
    }
    if (result.status !== 'succeeded' || !result.batch_id) {
        throw new Error(result.error_message ?? 'Batch import did not complete.');
    }
    return getBatchCatalog(result.batch_id);
}

export async function uploadBatchCsv(
    file: File,
    onProgress: (loaded: number, total: number) => void,
): Promise<BatchCatalog> {
    const capabilities = await requestJson<{ direct_uploads: boolean }>('/batch-imports/capabilities');
    if (capabilities.direct_uploads) return uploadDirectBatch(file, onProgress);
    return new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open('POST', `${API_BASE}/batches/import`);
        const token = getAccessToken();
        if (token) request.setRequestHeader('Authorization', `Bearer ${token}`);
        request.upload.onprogress = (event) => {
            if (event.lengthComputable) onProgress(event.loaded, event.total);
        };
        request.onerror = () => reject(new Error('Network error while uploading the batch CSV.'));
        request.onabort = () => reject(new DOMException('Upload cancelled.', 'AbortError'));
        request.onload = () => {
            if (request.status === 401) handleUnauthorized(token);
            let payload: unknown;
            try {
                payload = JSON.parse(request.responseText);
            } catch {
                reject(new Error(`Batch import failed (${request.status}).`));
                return;
            }
            if (request.status < 200 || request.status >= 300) {
                const detail = payload && typeof payload === 'object' && 'detail' in payload
                    ? (payload as { detail: unknown }).detail
                    : `Batch import failed (${request.status}).`;
                reject(new Error(typeof detail === 'string' ? detail : JSON.stringify(detail)));
                return;
            }
            resolve(payload as BatchCatalog);
        };
        const body = new FormData();
        body.append('file', file);
        request.send(body);
    });
}
import { authenticatedFetch, getAccessToken, handleUnauthorized } from './auth';
