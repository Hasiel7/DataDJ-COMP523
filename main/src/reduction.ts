import Papa from 'papaparse';
import {
    checkHealth,
    getJobStatus,
    submitJob,
    type Algorithm,
    type AlgorithmConfig,
    type DatasetMetadata,
    uploadDataset,
} from './reduction-api';
import './reduction.css';

function element<T extends HTMLElement>(id: string): T {
    const target = document.getElementById(id);
    if (!target) throw new Error(`Required page element #${id} was not found.`);
    return target as T;
}

const csvInput = element<HTMLInputElement>('csv-file');
const fileName = element<HTMLElement>('file-name');
const fileHint = element<HTMLElement>('file-hint');
const labelColumn = element<HTMLSelectElement>('label-column');
const rowIdColumn = element<HTMLSelectElement>('row-id-column');
const identifierColumns = element<HTMLDivElement>('identifier-columns');
const preprocessing = element<HTMLSelectElement>('preprocessing');
const hasHeader = element<HTMLInputElement>('has-header');
const maxSamples = element<HTMLInputElement>('max-samples');
const seedInput = element<HTMLInputElement>('seed');
const prepareButton = element<HTMLButtonElement>('prepare-button');
const generateButton = element<HTMLButtonElement>('generate-button');
const datasetMetadata = element<HTMLDivElement>('dataset-metadata');
const sourceStatus = element<HTMLSpanElement>('source-status');
const resultStatus = element<HTMLDivElement>('result-status');
const embeddingMetadata = element<HTMLDivElement>('embedding-metadata');
const exportResult = element<HTMLDivElement>('export-result');
const jobInstructions = element<HTMLDivElement>('job-instructions');
const jobMessage = element<HTMLParagraphElement>('job-message');
const workerCommand = element<HTMLElement>('worker-command');
const jobArtifact = element<HTMLParagraphElement>('job-artifact');
const errorMessage = element<HTMLDivElement>('error-message');
const dimensions = element<HTMLInputElement>('dimensions');
const dimensionsValue = element<HTMLOutputElement>('dimensions-value');
const apiIndicator = element<HTMLSpanElement>('api-indicator');

let selectedFile: File | null = null;
let csvColumns: string[] = [];
let loadedDataset: DatasetMetadata | null = null;

function setError(message: string | null): void {
    errorMessage.textContent = message ?? '';
    errorMessage.classList.toggle('hidden', message === null);
}

function setStatus(target: HTMLElement, message: string): void {
    target.textContent = message;
}

function setBusy(busy: boolean, operation: 'prepare' | 'generate'): void {
    if (operation === 'prepare') {
        prepareButton.disabled = busy || selectedFile === null || csvColumns.length === 0;
        prepareButton.classList.toggle('is-loading', busy);
        prepareButton.innerHTML = busy
            ? '<span class="button-spinner" aria-hidden="true"></span> Preparing…'
            : 'Prepare dataset <span aria-hidden="true">→</span>';
    } else {
        generateButton.disabled = busy || loadedDataset === null;
        generateButton.classList.toggle('is-loading', busy);
        generateButton.innerHTML = busy
            ? '<span class="button-spinner" aria-hidden="true"></span> Queueing…'
            : '<span class="generate-mark" aria-hidden="true">▶</span> Queue batch job';
    }
}

function fillSelect(select: HTMLSelectElement, columns: string[], blankText: string): void {
    select.replaceChildren(new Option(blankText, ''));
    for (const column of columns) select.add(new Option(column, column));
    select.disabled = columns.length === 0;
}

function renderIdentifierColumns(columns: string[]): void {
    identifierColumns.replaceChildren();
    identifierColumns.classList.toggle('empty-options', columns.length === 0);
    if (columns.length === 0) {
        identifierColumns.textContent = 'Select a CSV to choose columns';
        return;
    }

    for (const column of columns) {
        const label = document.createElement('label');
        label.className = 'check-chip';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.name = 'identifier-column';
        checkbox.value = column;
        const text = document.createElement('span');
        text.textContent = column;
        label.append(checkbox, text);
        identifierColumns.append(label);
    }
}

function selectedIdentifierColumns(): string[] {
    return Array.from(identifierColumns.querySelectorAll<HTMLInputElement>(
        'input[name="identifier-column"]:checked',
    ), (input) => input.value);
}

function inspectCsv(file: File): void {
    setError(null);
    fileName.textContent = file.name;
    fileHint.textContent = `${(file.size / 1024).toFixed(1)} KiB · reading column names…`;
    selectedFile = file;
    loadedDataset = null;
    datasetMetadata.replaceChildren();
    embeddingMetadata.textContent = 'No embedding generated';
    generateButton.disabled = true;
    sourceStatus.textContent = 'Inspecting CSV';
    setBusy(true, 'prepare');

    Papa.parse<Record<string, string> | string[]>(file, {
        header: hasHeader.checked,
        preview: 1,
        skipEmptyLines: true,
        complete: (result) => {
            const firstPreviewRow = result.data[0];
            csvColumns = hasHeader.checked
                ? result.meta.fields ?? []
                : Array.isArray(firstPreviewRow)
                    ? Array.from({ length: firstPreviewRow.length }, (_, index) => `column_${index}`)
                    : [];
            if (result.errors.length > 0 || csvColumns.length === 0) {
                selectedFile = null;
                csvColumns = [];
                sourceStatus.textContent = 'Could not read CSV';
                fileHint.textContent = hasHeader.checked
                    ? 'Check that the file has a header row.'
                    : 'Check that the file contains comma-separated values.';
                setError(result.errors[0]?.message ?? 'No CSV columns were found.');
                setBusy(false, 'prepare');
            } else {
                fillSelect(labelColumn, csvColumns, 'No label');
                fillSelect(rowIdColumn, csvColumns, 'Use row position');
                renderIdentifierColumns(csvColumns);
                const likelyId = hasHeader.checked
                    ? csvColumns.find((column) => /(^id$|_id$|sample|subject|patient|record)/i.test(column))
                    : undefined;
                if (likelyId) rowIdColumn.value = likelyId;
                fileHint.textContent = `${csvColumns.length} ${hasHeader.checked ? 'columns' : 'headerless columns'} · ${(file.size / 1024).toFixed(1)} KiB`;
                sourceStatus.textContent = 'CSV ready';
                setBusy(false, 'prepare');
            }
        },
        error: (error) => {
            selectedFile = null;
            csvColumns = [];
            sourceStatus.textContent = 'Could not read CSV';
            setError(error.message);
            setBusy(false, 'prepare');
        },
    });
}

async function prepareDataset(): Promise<void> {
    if (!selectedFile) return;
    setError(null);
    const randomSeed = Number(seedInput.value);
    const maximum = maxSamples.value.trim() === '' ? null : Number(maxSamples.value);
    if (!Number.isInteger(randomSeed) || randomSeed < 0) {
        setError('Seed must be a non-negative integer.');
        seedInput.focus();
        return;
    }
    if (maximum !== null && (!Number.isInteger(maximum) || maximum < 1 || maximum > 100000)) {
        setError('Maximum observations must be an integer from 1 to 100,000.');
        maxSamples.focus();
        return;
    }

    setBusy(true, 'prepare');
    sourceStatus.textContent = 'Preparing dataset';
    try {
        loadedDataset = await uploadDataset(selectedFile, {
            labelColumn: labelColumn.value || null,
            rowIdColumn: rowIdColumn.value || null,
            identifierColumns: selectedIdentifierColumns(),
            preprocessing: preprocessing.value as 'none' | 'standardize',
            maxSamples: maximum,
            randomSeed,
            hasHeader: hasHeader.checked,
        });
        renderDatasetMetadata(loadedDataset);
        sourceStatus.textContent = 'Dataset prepared';
        setStatus(resultStatus, 'Ready to compute');
        generateButton.disabled = false;
        exportResult.classList.add('is-ready');
        exportResult.querySelector('h3')!.textContent = 'Dataset prepared';
        exportResult.querySelector('p')!.textContent =
            'Choose a method and dimensions, then generate a CSV for the separate visualization workflow.';
        embeddingMetadata.replaceChildren();
    } catch (error) {
        loadedDataset = null;
        sourceStatus.textContent = 'Preparation failed';
        setError(error instanceof Error ? error.message : 'Dataset preparation failed.');
    } finally {
        setBusy(false, 'prepare');
    }
}

function renderDatasetMetadata(metadata: DatasetMetadata): void {
    const entries = [
        [`${metadata.observation_count.toLocaleString()}`, 'observations'],
        [`${metadata.feature_count.toLocaleString()}`, 'features'],
        [metadata.preprocessing === 'standardize' ? 'Standardized' : 'Unscaled', 'preprocessing'],
        [metadata.subsampled ? `Seed ${metadata.random_seed}` : 'Full dataset', metadata.subsampled ? 'subsample' : 'rows'],
    ];
    datasetMetadata.replaceChildren();
    const identity = document.createElement('div');
    identity.className = 'dataset-identity';
    const title = document.createElement('strong');
    title.textContent = metadata.filename;
    const subtitle = document.createElement('small');
    subtitle.textContent = `${metadata.dataset_id} · ${metadata.feature_columns.join(', ')}`;
    identity.append(title, subtitle);
    datasetMetadata.append(identity);
    for (const [value, caption] of entries) {
        const item = document.createElement('div');
        item.className = 'metadata-stat';
        const strong = document.createElement('strong');
        strong.textContent = value;
        const small = document.createElement('small');
        small.textContent = caption;
        item.append(strong, small);
        datasetMetadata.append(item);
    }
}

function selectedAlgorithm(): Algorithm {
    const selected = document.querySelector<HTMLInputElement>('input[name="algorithm"]:checked');
    if (!selected) throw new Error('Choose a dimensionality-reduction method.');
    return selected.value as Algorithm;
}

function numberValue(id: string): number {
    const input = element<HTMLInputElement>(id);
    if (input.value.trim() === '') {
        throw new Error(`Enter a value for ${input.closest('label')?.innerText ?? id}.`);
    }
    const value = Number(input.value);
    if (!Number.isFinite(value)) throw new Error(`Enter a valid value for ${input.closest('label')?.innerText ?? id}.`);
    return value;
}

function algorithmConfig(): AlgorithmConfig {
    const algorithm = selectedAlgorithm();
    if (algorithm === 'umap') {
        return {
            algorithm,
            n_components: Number(dimensions.value) as 2 | 3,
            n_neighbors: numberValue('umap-neighbors'),
            min_dist: numberValue('umap-min-dist'),
            n_epochs: numberValue('umap-epochs'),
        };
    }
    if (algorithm === 'tsne') {
        return {
            algorithm,
            n_components: Number(dimensions.value) as 2 | 3,
            perplexity: numberValue('tsne-perplexity'),
            learning_rate: numberValue('tsne-learning-rate'),
            max_iter: numberValue('tsne-max-iter'),
        };
    }
    return {
        algorithm,
        n_components: Number(dimensions.value) as 2 | 3,
        knn: numberValue('phate-knn'),
        t: numberValue('phate-t'),
        decay: numberValue('phate-decay'),
    };
}

async function queueBatchJob(): Promise<void> {
    if (!loadedDataset) return;
    setError(null);
    const randomSeed = Number(seedInput.value);
    if (!Number.isInteger(randomSeed) || randomSeed < 0) {
        setError('Seed must be a non-negative integer.');
        seedInput.focus();
        return;
    }

    setBusy(true, 'generate');
    setStatus(resultStatus, 'Submitting job');
    try {
        const config = algorithmConfig();
        const singleValueRange = (value: number) => ({
            start: String(value),
            stop: String(value),
            step: '1',
        });
        const parameters: Record<string, ReturnType<typeof singleValueRange>> = {};
        for (const [name, value] of Object.entries(config)) {
            if (name !== 'algorithm' && name !== 'n_components') {
                parameters[name] = singleValueRange(value);
            }
        }

        const submitted = await submitJob({
            dataset_id: loadedDataset.dataset_id,
            algorithms: { [config.algorithm]: parameters },
            n_components: config.n_components,
            random_seed: randomSeed,
        });

        jobInstructions.classList.remove('hidden');
        jobMessage.textContent = `Job ${submitted.job_id} is queued. Run the command below in a backend terminal.`;
        workerCommand.textContent =
            `Push-Location backend; uv run --project . python -m app.worker_cli ${submitted.job_id}; Pop-Location`;
        jobArtifact.textContent = '';
        exportResult.classList.add('is-ready');
        exportResult.querySelector('h3')!.textContent = 'Batch job queued';
        exportResult.querySelector('p')!.textContent =
            'The worker will compute the saved configuration and publish a ZIP containing the combined CSV and manifest.';
        setStatus(resultStatus, 'Queued');
        void pollJobStatus(submitted.job_id);
    } catch (error) {
        setStatus(resultStatus, 'Submission failed');
        setError(error instanceof Error ? error.message : 'Job submission failed.');
    } finally {
        setBusy(false, 'generate');
    }
}

function delay(milliseconds: number): Promise<void> {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function pollJobStatus(jobId: string): Promise<void> {
    while (true) {
        await delay(2500);
        try {
            const job = await getJobStatus(jobId);
            setStatus(resultStatus, `${job.status.toUpperCase()} · ${job.job_id}`);

            if (job.status === 'succeeded') {
                jobMessage.textContent = 'Job completed successfully.';
                jobArtifact.textContent = job.artifact_path
                    ? `Package available on the backend machine: ${job.artifact_path}`
                    : 'Job completed, but no artifact path was recorded.';
                return;
            }
            if (job.status === 'failed' || job.status === 'cancelled') {
                jobMessage.textContent = `Job finished with status: ${job.status}.`;
                return;
            }
        } catch (error) {
            setStatus(resultStatus, 'Status check failed');
            setError(error instanceof Error ? error.message : 'Could not retrieve job status.');
            return;
        }
    }
}

function setAlgorithmParameters(algorithm: Algorithm): void {
    for (const method of ['umap', 'tsne', 'phate'] as const) {
        element<HTMLDivElement>(`parameters-${method}`).classList.toggle('hidden', method !== algorithm);
    }
}

csvInput.addEventListener('change', () => {
    const file = csvInput.files?.[0];
    if (file) inspectCsv(file);
});

hasHeader.addEventListener('change', () => {
    const file = csvInput.files?.[0];
    if (file) inspectCsv(file);
});

prepareButton.addEventListener('click', () => void prepareDataset());
generateButton.addEventListener('click', () => void queueBatchJob());

document.querySelectorAll<HTMLInputElement>('input[name="algorithm"]').forEach((radio) => {
    radio.addEventListener('change', () => setAlgorithmParameters(radio.value as Algorithm));
});

dimensions.addEventListener('input', () => {
    dimensionsValue.value = `${dimensions.value}D`;
});

void checkHealth().then(() => {
    apiIndicator.classList.add('online');
    apiIndicator.innerHTML = '<i></i> API connected';
}).catch(() => {
    apiIndicator.classList.add('offline');
    apiIndicator.innerHTML = '<i></i> API unavailable';
});
