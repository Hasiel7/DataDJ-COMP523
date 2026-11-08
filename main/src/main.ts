import { loadFile, loadOriginalFile, getOriginalData } from './fileHandler';
import { initSamples } from './samples';
import { animate } from './render';
import * as renderer from './render';
import { initDropdowns, recolor, updateOriginalColorOptions } from './header';
import { initBoard } from './board';
import { initFavorites } from './favorites';
import { initAccount } from './account';
import { initTooltip } from './tooltip';
import { initAxes } from './axes';
import * as midi from './midi'
import * as toast from './toast';
import * as auth from './auth';

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

    // Reset original-column selections when a new processed CSV is loaded.
    selectedOriginalColumns.clear();

    const selectedLabel = document.getElementById('original-column-selected');

    if (selectedLabel) {
        selectedLabel.textContent = 'Selected: None';
    }

    updateOriginalColumnOptions(originalColumnSearch.value);
    syncOriginalColorOptions();
}
inputEl.addEventListener('change', async () => {
    const file = inputEl.files?.[0]
    if (!file) return
    // Cleared so choosing the same file again (e.g. after a sample) still fires change
    inputEl.value = ''
    await openFile(file)
})
initSamples(openFile)

const originalColumnSearch = document.getElementById('original-column-search') as HTMLInputElement;
const originalColumnList = document.getElementById('original-column-list') as HTMLDivElement;

let originalColumnNames: string[] = [];
const selectedOriginalColumns = new Set<string>();

function updateOriginalColumnOptions(searchText: string = '') {
    originalColumnList.replaceChildren();

    const matches = originalColumnNames.filter(name =>
        name.toLowerCase().includes(searchText.toLowerCase().trim())
    );

    if (matches.length === 0) {
        const message = document.createElement('div');
        message.textContent = 'No matching columns';
        originalColumnList.appendChild(message);
        return;
    }

    for (const name of matches) {
        const option = document.createElement('button');
        option.type = 'button';
        option.textContent = name;
        option.className = 'original-column-option';
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', String(selectedOriginalColumns.has(name)));

        
        option.addEventListener('click', () => {
            if (selectedOriginalColumns.has(name)) {
                selectedOriginalColumns.delete(name);
            } else {
                selectedOriginalColumns.add(name);
            }

            const selectedLabel = document.getElementById('original-column-selected');

            if (selectedLabel) {
                selectedLabel.textContent =
                    `${selectedOriginalColumns.size} column(s) selected`;
            }

            updateOriginalColumnOptions(originalColumnSearch.value);
            syncOriginalColorOptions();
        });

        originalColumnList.appendChild(option);
    }
}

function syncOriginalColorOptions() {
    updateOriginalColorOptions(Array.from(selectedOriginalColumns));
}


originalColumnSearch.addEventListener('input', () => {
    updateOriginalColumnOptions(originalColumnSearch.value);
});
const clearOriginalColumnsButton = document.getElementById('original-column-clear') as HTMLButtonElement;

clearOriginalColumnsButton.addEventListener('click', () => {
    selectedOriginalColumns.clear();

    const selectedLabel = document.getElementById('original-column-selected');

    if (selectedLabel) {
        selectedLabel.textContent = 'Selected: None';
    }

    updateOriginalColumnOptions(originalColumnSearch.value);
    syncOriginalColorOptions();
});

const originalInputEl = document.getElementById('original-input-file') as HTMLInputElement;
const originalFileNameEl = document.getElementById('original-file-name') as HTMLSpanElement;

originalInputEl.addEventListener('change', async () => {
    const file = originalInputEl.files?.[0];
    if (!file) return;

    originalInputEl.value = '';

    try {
        const hasHeaders = (
            document.getElementById('original-has-headers') as HTMLInputElement
        ).checked;
        const rowCount = await loadOriginalFile(file, hasHeaders);

        
        const data = getOriginalData();

        if (data) {
            originalColumnNames = data[0];
            selectedOriginalColumns.clear();
            const selectedLabel = document.getElementById('original-column-selected');

            if (selectedLabel) {
                selectedLabel.textContent = 'Selected: None';
            }

            originalColumnSearch.value = '';
            originalColumnSearch.disabled = false;

            updateOriginalColumnOptions();
            syncOriginalColorOptions();
        }


        originalFileNameEl.textContent = file.name;
        originalFileNameEl.title = file.name;

        toast.newMessage(`Original dataset loaded: ${rowCount} rows`);
    } catch (error) {
        toast.newError(
            error instanceof Error ? error.message : 'Could not load original dataset.'
        );
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
