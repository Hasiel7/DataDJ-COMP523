import { loadFile } from './fileHandler';
import { initSamples } from './samples';
import { animate } from './render';
import * as renderer from './render';
import { initDropdowns, recolor } from './header';
import { initBoard } from './board';
import { initFavorites } from './favorites';
import { initAccount } from './account';
import { initTooltip } from './tooltip';
import { initAxes } from './axes';
import * as midi from './midi'
import * as toast from './toast';
import * as auth from './auth';
import { uploadBatchCsv } from './reduction-api';
import { initRemoteBatch } from './runs';

window.onerror = (msg) => {
    toast.newError(`Something went wrong: ${msg}`)
}

window.addEventListener('unhandledrejection', (event) => { toast.newError(`Something went wrong: ${event.reason}`) })

//midi
const midiStatusEl = document.getElementById('midi-status') as HTMLSpanElement;
const midiStatusText: Record<midi.MidiStatus, [label: string, tooltip: string]> = {
    pending: ['MIDI…', 'Waiting for MIDI permission.'],
    unsupported: ['No MIDI in this browser', "Safari and Firefox don't support Web MIDI. Open this page in Chrome or Edge to use a controller."],
    denied: ['MIDI blocked', "MIDI access was refused. Allow it in your browser's site settings, then reload."],
    'no-device': ['No MIDI device', 'Plug in a controller. It will connect automatically.'],
    connected: ['MIDI', 'Controller connected.'],
};
midi.onStatusChange((status, deviceName) => {
    const [label, tooltip] = midiStatusText[status];
    midiStatusEl.dataset.state = status;
    midiStatusEl.textContent = deviceName ? `${label} · ${deviceName}` : label;
    midiStatusEl.title = tooltip;
    // Bind buttons, Knobs and the board panel only make sense with a controller (see style.css)
    document.body.classList.toggle('midi-connected', status === 'connected');
})
midi.init()

// Hides the account area and Reduction lab links in builds with no API (see auth.hasBackend)
document.body.classList.toggle('no-backend', !auth.hasBackend)

// Header
initDropdowns()
initBoard()
initFavorites()
initAccount()
const inputEl: HTMLInputElement = document.getElementById("input-file") as HTMLInputElement;
const fileNameEl = document.getElementById('file-name') as HTMLSpanElement;
const emptyStateEl = document.getElementById('empty-state') as HTMLDivElement;
async function openFile(file: File) {
    // Stays up until the file is drawn; large files take longer than a normal toast
    toast.newMessage('Loading file...', 60_000)
    await loadFile(file)
    toast.closeToast()
    fileNameEl.textContent = fileNameEl.title = file.name
    emptyStateEl.classList.add('hidden')
}
inputEl.addEventListener('change', async () => {
    const file = inputEl.files?.[0]
    if (!file) return
    // Cleared so choosing the same file again (e.g. after a sample) still fires change
    inputEl.value = ''
    await openFile(file)
})
initSamples(openFile)

const openBatchButton = document.getElementById('open-batch-btn') as HTMLButtonElement;
const batchDialog = document.getElementById('batch-dialog') as HTMLDialogElement;
const batchForm = document.getElementById('batch-form') as HTMLFormElement;
const batchFileInput = document.getElementById('batch-file') as HTMLInputElement;
const batchCancel = document.getElementById('batch-cancel') as HTMLButtonElement;
const batchSubmit = document.getElementById('batch-submit') as HTMLButtonElement;
const batchProgress = document.getElementById('batch-progress') as HTMLProgressElement;
const batchProgressLabel = document.getElementById('batch-progress-label') as HTMLParagraphElement;
const batchError = document.getElementById('batch-error') as HTMLParagraphElement;
openBatchButton.addEventListener('click', () => {
    batchForm.reset();
    batchProgress.classList.add('hidden');
    batchProgressLabel.textContent = '';
    batchError.classList.add('hidden');
    batchDialog.showModal();
    batchFileInput.focus();
});
batchCancel.addEventListener('click', () => batchDialog.close());
batchForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const file = batchFileInput.files?.[0];
    if (!file) return;
    openBatchButton.disabled = true;
    batchSubmit.disabled = true;
    batchCancel.disabled = true;
    batchProgress.classList.remove('hidden');
    batchProgress.value = 0;
    batchError.classList.add('hidden');
    try {
        batchProgressLabel.textContent = `Uploading ${(file.size / 1024 / 1024).toFixed(1)} MiB…`;
        const catalog = await uploadBatchCsv(file, (loaded, total) => {
            batchProgress.value = total > 0 ? loaded / total * 100 : 0;
            batchProgressLabel.textContent = loaded < total
                ? `Uploading ${(loaded / 1024 / 1024).toFixed(1)} of ${(total / 1024 / 1024).toFixed(1)} MiB`
                : 'Upload complete. Indexing runs…';
        });
        initRemoteBatch(catalog.batch_id, catalog);
        batchDialog.close();
        fileNameEl.textContent = fileNameEl.title = `${catalog.filename} · ${catalog.runs.length} runs`;
        emptyStateEl.classList.add('hidden');
    } catch (error) {
        const message = error instanceof Error ? error.message : 'Could not import batch CSV.';
        batchError.textContent = message;
        batchError.classList.remove('hidden');
        toast.newError(message);
    } finally {
        openBatchButton.disabled = false;
        batchSubmit.disabled = false;
        batchCancel.disabled = false;
    }
});


// Plot background, remembered in this browser
const backgroundSelect = document.getElementById('background-select') as HTMLSelectElement;
const setBackground = (mode: 'dark' | 'light') => {
    document.body.classList.toggle('light-stage', mode === 'light');
    renderer.setBackground(mode);
    recolor(); // plasma's usable range depends on the background
    localStorage.setItem('datadj.background', mode);
}
backgroundSelect.value = localStorage.getItem('datadj.background') === 'light' ? 'light' : 'dark';
setBackground(backgroundSelect.value as 'dark' | 'light');
backgroundSelect.onchange = () => setBackground(backgroundSelect.value as 'dark' | 'light');

// Rendering
const canvas: HTMLCanvasElement = document.getElementById('canvas') as HTMLCanvasElement;
animate(canvas)
initTooltip(canvas)
initAxes()
