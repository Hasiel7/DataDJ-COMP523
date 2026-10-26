import { initFileInput } from './fileHandler';
import { animate } from './render';
import { initDropdowns } from './header';
import { initBoard } from './board';
import * as midi from './midi'
import * as toast from './toast';

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
})
midi.init()

// Header
initDropdowns()
initBoard()
const inputEl: HTMLInputElement = document.getElementById("input-file") as HTMLInputElement;
const fileNameEl = document.getElementById('file-name') as HTMLSpanElement;
const emptyStateEl = document.getElementById('empty-state') as HTMLDivElement;
inputEl.addEventListener('change', async () => {
    toast.newMessage('Loading file...')
    await initFileInput(inputEl)
    toast.closeToast()
    const fileName = inputEl.files?.[0]?.name ?? ''
    fileNameEl.textContent = fileNameEl.title = fileName
    emptyStateEl.classList.add('hidden')
}
)


// Rendering
const canvas: HTMLCanvasElement = document.getElementById('canvas') as HTMLCanvasElement;
animate(canvas)
