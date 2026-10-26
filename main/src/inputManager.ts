import * as midi from "./midi";

// Central registry for MIDI bindings. Features register named actions, and
// any physical control (identified by its MIDI signature) can be bound to one.
// The board panel and the Bind buttons are both views onto this registry.

export type Step = 1 | -1;
export type ControlKind = 'knob' | 'slider' | 'button';

export interface Action {
    id: string;
    label: string;
    group: string;
    run: (step: Step) => void;
}

export interface Binding {
    actionId: string;
    reverse: boolean;
}

export interface MidiMsg {
    // Port + channel + note/CC number, e.g. "SINCO VMX8-Master|cc:1:40".
    // Note-on and note-off share a key.
    key: string;
    value: number;
}

// What the board panel knows about a signature, once that control is identified
export interface ControlInfo {
    name: string;
    kind: ControlKind;
}

const actions = new Map<string, Action>();
const bindings = new Map<string, Binding>();
const controlInfo = new Map<string, ControlInfo>();
const changeListeners = new Set<() => void>();
const messageListeners = new Set<(msg: MidiMsg) => void>();
const boundButtons = new Map<HTMLButtonElement, string>();

// The VMX8's knobs and sliders both send their position (0–127), not a
// direction, so we turn changes in position into steps
const anchors = new Map<string, number>();
const knobStepSize = 1; // one click of the knob
const sliderStepSize = 4; // a full slide is about 32 steps

// While capturing, MIDI goes to the capturer instead of triggering actions
interface Capture {
    onMessage: (msg: MidiMsg) => boolean; // return true when done
    onCancel?: () => void;
    actionId?: string; // set when a Bind button is waiting
}
let capture: Capture | null = null;

export function parseMidi(data: Uint8Array | null, portName: string): MidiMsg | null {
    if (!data || data.length < 2) return null;
    const type = data[0] & 0xf0;
    const ch = (data[0] & 0x0f) + 1;
    const value = data[2] ?? 0;
    const port = `${portName}|`;
    switch (type) {
        case 0x80: return { key: `${port}note:${ch}:${data[1]}`, value: 0 };
        case 0x90: return { key: `${port}note:${ch}:${data[1]}`, value };
        case 0xb0: return { key: `${port}cc:${ch}:${data[1]}`, value };
        case 0xc0: return { key: `${port}pc:${ch}:${data[1]}`, value: 127 }; // Program change: a press with no release
        case 0xe0: return { key: `${port}pb:${ch}`, value };
        default: return null;
    }
}

function splitKey(key: string) {
    const bar = key.lastIndexOf('|');
    const [type, ch, num] = key.slice(bar + 1).split(':');
    return { port: key.slice(0, bar), type, ch, num };
}

// "SINCO VMX8-Master" -> "Master"
export function shortPortName(portName: string): string {
    return portName.split(/[-\s]/).pop() || portName;
}

const statusNames: Record<number, string> = {
    0x80: 'Note off', 0x90: 'Note on', 0xa0: 'Aftertouch', 0xb0: 'CC',
    0xc0: 'Program change', 0xd0: 'Channel pressure', 0xe0: 'Pitch bend',
};

// Human-readable description of any MIDI message, including ones we can't bind
export function describeRaw(data: Uint8Array): string {
    const hex = Array.from(data.slice(0, 12), (b) => b.toString(16).padStart(2, '0').toUpperCase()).join(' ');
    const more = data.length > 12 ? ` … (${data.length} bytes)` : '';
    let kind: string;
    if (data[0] === 0xf0) kind = 'SysEx (not bindable)';
    else if (data[0] >= 0xf0) kind = 'System message';
    else kind = `${statusNames[data[0] & 0xf0]} · ch ${(data[0] & 0x0f) + 1}`;
    return `${hex}${more} · ${kind}`;
}

export function describeKey(key: string): string {
    const { port, type, ch, num } = splitKey(key);
    const where = `ch ${ch} · ${shortPortName(port)}`;
    if (type === 'note') return `Note ${num} · ${where}`;
    if (type === 'cc') return `CC ${num} · ${where}`;
    if (type === 'pc') return `Program change ${num} · ${where}`;
    return `Pitch bend · ${where}`;
}

function kindFor(key: string): ControlKind {
    const known = controlInfo.get(key)?.kind;
    if (known) return known;
    const { type } = splitKey(key);
    if (type === 'note' || type === 'pc') return 'button';
    if (type === 'pb') return 'slider';
    return 'knob';
}

// Is this message a deliberate actuation (not a button release)?
export function isActuation(msg: MidiMsg): boolean {
    return kindFor(msg.key) !== 'button' || msg.value > 0;
}

function toStep(msg: MidiMsg): Step | null {
    switch (kindFor(msg.key)) {
        case 'button':
            return msg.value > 0 ? 1 : null;
        case 'knob':
            return positionStep(msg, knobStepSize);
        case 'slider':
            return positionStep(msg, sliderStepSize);
    }
}

// Up (clockwise) is +1, down is -1. The first message only records where the control is.
function positionStep(msg: MidiMsg, stepSize: number): Step | null {
    const anchor = anchors.get(msg.key);
    if (anchor === undefined) {
        anchors.set(msg.key, msg.value);
        return null;
    }
    if (Math.abs(msg.value - anchor) < stepSize) return null;
    anchors.set(msg.key, msg.value);
    return msg.value > anchor ? 1 : -1;
}

midi.addMidiEventListener((data, portName) => {
    const msg = parseMidi(data, portName);
    if (!msg) return;
    messageListeners.forEach((listener) => listener(msg));

    if (capture) {
        const current = capture;
        if (current.onMessage(msg) && capture === current) {
            capture = null;
            notify();
        }
        return;
    }

    const binding = bindings.get(msg.key);
    const step = toStep(msg);
    if (!binding || step === null) return;
    actions.get(binding.actionId)?.run(binding.reverse ? (-step as Step) : step);
});

// Clicking elsewhere cancels a Bind button that is waiting for a control
window.addEventListener('click', () => {
    if (capture?.actionId) cancelCapture();
});

function notify() {
    refreshButtons();
    changeListeners.forEach((listener) => listener());
}

export function onChange(listener: () => void) {
    changeListeners.add(listener);
}

export function onMessage(listener: (msg: MidiMsg) => void) {
    messageListeners.add(listener);
}

// Registering an existing id replaces its handler and keeps its bindings,
// so it is safe to call again when a new file is loaded.
export function registerAction(action: Action) {
    actions.set(action.id, action);
    notify();
}

export function unregisterAction(id: string) {
    actions.delete(id);
    for (const [key, binding] of bindings) {
        if (binding.actionId === id) bindings.delete(key);
    }
    notify();
}

export function getActions(): Action[] {
    return Array.from(actions.values());
}

export function getBinding(key: string): Binding | undefined {
    return bindings.get(key);
}

export function getAction(id: string): Action | undefined {
    return actions.get(id);
}

export function setBinding(key: string, binding: Binding | null) {
    if (binding) bindings.set(key, binding);
    else bindings.delete(key);
    notify();
}

export function moveBinding(fromKey: string, toKey: string) {
    const binding = bindings.get(fromKey);
    if (!binding) return;
    bindings.delete(fromKey);
    bindings.set(toKey, binding);
    notify();
}

export function setControlInfo(key: string, info: ControlInfo | null) {
    if (info) controlInfo.set(key, info);
    else controlInfo.delete(key);
    notify();
}

export function startCapture(next: Capture) {
    cancelCapture();
    capture = next;
    notify();
}

export function cancelCapture() {
    if (!capture) return;
    const current = capture;
    capture = null;
    current.onCancel?.();
    notify();
}

// Wire a Bind button to an action. Assigned rather than added so calling
// this again for the same button (e.g. on a new file) doesn't stack handlers.
export function attachBindButton(button: HTMLButtonElement, actionId: string) {
    boundButtons.set(button, actionId);
    button.onclick = (e) => {
        e.stopPropagation(); // Prevent the window click from cancelling immediately
        if (capture?.actionId === actionId) {
            cancelCapture();
            return;
        }
        startCapture({
            actionId,
            onMessage: (msg) => {
                if (!isActuation(msg)) return false;
                // A Bind button owns its action: replace whatever it was bound to
                for (const [key, binding] of bindings) {
                    if (binding.actionId === actionId) bindings.delete(key);
                }
                bindings.set(msg.key, { actionId, reverse: false });
                return true;
            },
        });
    };
    refreshButtons();
}

function refreshButtons() {
    for (const [button, actionId] of boundButtons) {
        if (!button.isConnected) {
            boundButtons.delete(button);
            continue;
        }
        let state: 'unbound' | 'waiting' | 'bound' = 'unbound';
        let text = 'Bind';
        const keys = Array.from(bindings).filter(([, b]) => b.actionId === actionId).map(([key]) => key);
        if (capture?.actionId === actionId) {
            state = 'waiting';
            text = 'Move a control…';
        } else if (keys.length > 0) {
            state = 'bound';
            text = controlInfo.get(keys[0])?.name ?? 'Bound';
            if (keys.length > 1) text += ` +${keys.length - 1}`;
        }
        button.textContent = text;
        button.dataset.state = state;
    }
}
