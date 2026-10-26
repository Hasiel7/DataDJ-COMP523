import * as input from './inputManager';
import * as toast from './toast';
import * as midi from './midi';

// Floating diagram of the SINCO VMX8 controller. Click a control to bind a
// function to it. Which MIDI message each physical control sends is learned
// by moving it ("identify"), and remembered in this browser.

const SVG_NS = 'http://www.w3.org/2000/svg';
// v2: keys include the MIDI port, so maps saved before that are dropped
const STORAGE_KEY = 'datadj.board.vmx8.v2';

// The VMX8's factory CC numbers (Private port, channel 1), from the
// walkthrough, so a fresh browser doesn't have to identify the board again.
// The board reuses numbers: knobs (30–37) share with S 3–8 and R 1–2, and
// sliders (40–47) with R 5–8 and ■ 1–4. The app can't tell those pairs
// apart, so only one control of each pair is listed.
// Unconfirmed, left out until checked: S 4 / R 4 (39?) and ■ 7 / ■ 8 (50, 51?).
const DEFAULT_PROFILE: Record<string, string> = (() => {
    const cc = (n: number) => `SINCO VMX8-Private|cc:1:${n}`;
    const map: Record<string, string> = {};
    for (let i = 1; i <= 8; i++) {
        map[`knob-${i}`] = cc(29 + i);
        map[`m-${i}`] = cc(19 + i);
    }
    for (let i = 1; i <= 4; i++) map[`slider-${i}`] = cc(39 + i);
    for (let i = 1; i <= 6; i++) map[`sq-${i}`] = cc(43 + i);
    map['s-1'] = cc(28);
    map['s-2'] = cc(29);
    map['r-3'] = cc(38);
    ['play', 'pause', 'record', 'rewind', 'forward', 'prev', 'next', 'up', 'down', 'left', 'right']
        .forEach((id, i) => { map[id] = cc(52 + i); });
    return map;
})();

type Icon = { d: string; filled: boolean };
type Shape =
    | { type: 'knob'; cx: number; cy: number; r: number }
    | { type: 'slider'; cx: number; top: number; h: number } // vertical fader
    | { type: 'button'; x: number; y: number; w: number; h: number; text?: string; icon?: Icon };

interface BoardControl {
    id: string;
    name: string;
    kind: input.ControlKind;
    shape: Shape;
    // Where the bound function's name is written, if there's room for it
    label?: { x: number; y: number; maxChars: number };
    // Handled inside the board; sends no MIDI, so it can't be identified or bound
    noMidi?: string;
}

interface ControlView {
    control: BoardControl;
    g: SVGGElement;
    title: SVGTitleElement;
    label?: SVGTextElement;
    mark?: SVGElement;
    flashTimer?: number;
}

// Icons are drawn around (0, 0) and moved to the button's center
const icons: Record<string, Icon> = {
    play: { d: 'M-6 -8 L8 0 L-6 8 Z', filled: true },
    pause: { d: 'M-7 -8 h5 v16 h-5 Z M2 -8 h5 v16 h-5 Z', filled: true },
    record: { d: 'M-7 0 a7 7 0 1 0 14 0 a7 7 0 1 0 -14 0', filled: true },
    rewind: { d: 'M0 -7 L-10 0 L0 7 Z M10 -7 L0 0 L10 7 Z', filled: true },
    forward: { d: 'M0 -7 L10 0 L0 7 Z M-10 -7 L0 0 L-10 7 Z', filled: true },
    prev: { d: 'M-1 -7 L-8 0 L-1 7 M8 -7 L1 0 L8 7', filled: false },
    next: { d: 'M1 -7 L8 0 L1 7 M-8 -7 L-1 0 L-8 7', filled: false },
    up: { d: 'M0 8 V-8 M-6 -2 L0 -8 L6 -2', filled: false },
    down: { d: 'M0 -8 V8 M-6 2 L0 8 L6 2', filled: false },
    left: { d: 'M8 0 H-8 M-2 -6 L-8 0 L-2 6', filled: false },
    right: { d: 'M-8 0 H8 M2 -6 L8 0 L2 6', filled: false },
    square: { d: 'M-7 -7 h14 v14 h-14 Z', filled: false },
};

const WIDTH = 1080;
const HEIGHT = 384;
const COL = 120; // one channel: a knob above a slider strip
const X0 = 20;

function buildLayout(): BoardControl[] {
    const list: BoardControl[] = [];

    // Row 1: eight knobs, then the BT / Shift stack
    for (let n = 1; n <= 8; n++) {
        const cx = X0 + (n - 1) * COL + COL / 2;
        list.push({ id: `knob-${n}`, name: `Knob ${n}`, kind: 'knob', shape: { type: 'knob', cx, cy: 56, r: 30 }, label: { x: cx, y: 108, maxChars: 16 } });
    }
    const sideX = X0 + 8 * COL + 10;
    list.push({ id: 'bt', name: 'BT', kind: 'button', shape: { type: 'button', x: sideX, y: 22, w: 70, h: 32, text: 'BT' }, noMidi: 'Bluetooth pairing. It is handled inside the board and sends no MIDI.' });
    list.push({ id: 'shift', name: 'Shift', kind: 'button', shape: { type: 'button', x: sideX, y: 66, w: 70, h: 32, text: 'SHIFT' }, noMidi: 'Sends no MIDI and does not change what the other controls send.' });

    // Row 2: eight vertical faders, each with a stacked M / S / R / square column
    for (let n = 1; n <= 8; n++) {
        const x = X0 + (n - 1) * COL;
        list.push({ id: `slider-${n}`, name: `Slider ${n}`, kind: 'slider', shape: { type: 'slider', cx: x + 46, top: 140, h: 130 }, label: { x: x + COL / 2, y: 296, maxChars: 14 } });
        const small: [string, string, string | undefined][] = [['m', 'M', 'M'], ['s', 'S', 'S'], ['r', 'R', 'R'], ['sq', 'Square', undefined]];
        small.forEach(([id, name, text], j) => {
            const shape: Shape = { type: 'button', x: x + 82, y: 146 + j * 32, w: 24, h: 24, text, icon: text ? undefined : icons.square };
            list.push({ id: `${id}-${n}`, name: `${name} ${n}`, kind: 'button', shape });
        });
    }

    // Row 3: transport and arrow buttons, centered
    const groups: [string, string][][] = [
        [['play', 'Play'], ['pause', 'Pause'], ['record', 'Record']],
        [['rewind', 'Rewind'], ['forward', 'Fast-forward']],
        [['prev', 'Previous'], ['next', 'Next']],
        [['up', 'Up'], ['down', 'Down'], ['left', 'Left'], ['right', 'Right']],
    ];
    const w = 54, gap = 22, groupGap = 44;
    const total = groups.flat().length * (w + gap) - gap + (groups.length - 1) * (groupGap - gap);
    let x = (WIDTH - total) / 2;
    const y = 322;
    groups.forEach((group, i) => {
        if (i > 0) x += groupGap - gap;
        for (const [id, name] of group) {
            list.push({ id, name, kind: 'button', shape: { type: 'button', x, y, w, h: 30, icon: icons[id] }, label: { x: x + w / 2, y: y + 46, maxChars: 9 } });
            x += w + gap;
        }
    });
    return list;
}

const layout = buildLayout();
// The controls the walkthrough asks for
const identifiable = layout.filter((control) => !control.noMidi);
const byId = new Map(layout.map((control) => [control.id, control]));
const views = new Map<string, ControlView>();

// Which MIDI signature each physical control sends
const profile = new Map<string, string>();
const keyToControl = new Map<string, string>();

type Mode =
    | { type: 'idle' }
    | { type: 'wizard'; index: number }
    | { type: 'learn'; controlId: string };
const idle: Mode = { type: 'idle' };
// When an identified control last sent anything (see learn)
let lastKnownActivity = 0;
const echoWindow = 250; // ms
let mode: Mode = idle;

let panel: HTMLElement;
let statusEl: HTMLElement;
let statusText: HTMLElement;
let monitorEl: HTMLElement;
let logEl: HTMLOListElement;
let logBtn: HTMLButtonElement;
const maxLogEntries = 40;
let identifyBtn: HTMLButtonElement;
let minimizeBtn: HTMLButtonElement;
let placed = false;
let savedHeight = '';

let picker: HTMLDivElement | null = null;
let pickerControl: BoardControl | null = null;
let pickerReverse = false;

function el<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}, text?: string): SVGElementTagNameMap[K] {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
    if (text) node.textContent = text;
    return node;
}

function truncate(text: string, maxChars: number) {
    return text.length > maxChars ? text.slice(0, maxChars - 1) + '…' : text;
}

function loadProfile() {
    let stored: Record<string, unknown> = {};
    try {
        stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
    } catch { /* storage blocked or corrupt; start fresh */ }
    for (const [id, key] of Object.entries({ ...DEFAULT_PROFILE, ...stored })) {
        const control = byId.get(id);
        if (!control || control.noMidi || typeof key !== 'string' || keyToControl.has(key)) continue;
        profile.set(id, key);
        keyToControl.set(key, id);
        input.setControlInfo(key, { name: control.name, kind: control.kind });
    }
}

function saveProfile() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(profile)));
    } catch { /* not fatal: the map just won't survive a reload */ }
}

// Record that `control` sends `key`, taking the key from another control if
// it had been identified wrongly
function identify(control: BoardControl, key: string) {
    const previousOwner = keyToControl.get(key);
    if (previousOwner === control.id) return;
    if (previousOwner) profile.delete(previousOwner);

    const oldKey = profile.get(control.id);
    if (oldKey) {
        keyToControl.delete(oldKey);
        input.setControlInfo(oldKey, null);
        // A function bound to this control on the diagram follows it to its real key
        if (!input.getBinding(key)) input.moveBinding(oldKey, key);
    }
    profile.set(control.id, key);
    keyToControl.set(key, control.id);
    input.setControlInfo(key, { name: control.name, kind: control.kind });
    saveProfile();
}

// Wait for the user to move `control`, then identify it. With `takeOver`, a
// key already identified as another control moves to this one (fixing a
// mistake); without it, other controls are ignored so a stray bump or a
// slider still settling from the previous step can't be claimed.
function learn(control: BoardControl, nextMode: Mode, onLearned: (key: string) => void, takeOver: boolean) {
    mode = nextMode;
    input.startCapture({
        onMessage: (msg) => {
            const owner = keyToControl.get(msg.key);
            if (owner && owner !== control.id && !takeOver) return false;
            // A control can send more than one message per move (e.g. on both
            // ports). Those companions arrive right with an identified control's
            // message and must not be claimed by the next step of the walkthrough.
            if (!owner && !takeOver && performance.now() - lastKnownActivity < echoWindow) return false;
            if (!input.isActuation(msg)) return false;
            identify(control, msg.key);
            lastKnownActivity = performance.now();
            mode = idle;
            onLearned(msg.key);
            render();
            return true;
        },
        onCancel: () => {
            if (mode !== nextMode) return; // replaced by the next step, not stopped
            mode = idle;
            render();
        },
    });
    render();
}

function wizardStep(index: number) {
    if (index >= identifiable.length) {
        input.cancelCapture(); // when Skip was pressed on the last control
        toast.newMessage('Every control is identified. Use Copy map to save it.');
        return;
    }
    learn(identifiable[index], { type: 'wizard', index }, () => wizardStep(index + 1), false);
}

function startWizard() {
    closePicker();
    const firstUnknown = identifiable.findIndex((control) => !profile.has(control.id));
    wizardStep(firstUnknown === -1 ? 0 : firstUnknown);
}

function learningId(): string | null {
    if (mode.type === 'wizard') return identifiable[mode.index].id;
    if (mode.type === 'learn') return mode.controlId;
    return null;
}

function bindingText(control: BoardControl, binding: input.Binding): string {
    const action = input.getAction(binding.actionId);
    if (!action) return '';
    if (control.kind === 'button') return `${action.label} ${binding.reverse ? '−' : '+'}`;
    return binding.reverse ? `${action.label} (rev)` : action.label;
}

function render() {
    if (!panel) return;
    const learning = learningId();
    for (const view of views.values()) {
        const { control } = view;
        const key = profile.get(control.id);
        const binding = key ? input.getBinding(key) : undefined;
        const text = binding ? bindingText(control, binding) : '';
        view.g.classList.toggle('is-identified', !!key);
        view.g.classList.toggle('is-bound', !!text);
        view.g.classList.toggle('is-learning', learning === control.id);
        view.g.classList.toggle('is-selected', pickerControl?.id === control.id);
        view.g.classList.toggle('is-disabled', !!control.noMidi);
        const description = control.noMidi
            ? `${control.name} · sends no MIDI`
            : [control.name, key ? input.describeKey(key) : 'not identified', text ? `→ ${text}` : 'unbound'].join(' · ');
        view.title.textContent = description;
        view.g.setAttribute('aria-label', description);
        if (view.label && control.label) view.label.textContent = truncate(text, control.label.maxChars);
    }

    statusEl.classList.toggle('hidden', mode.type !== 'wizard');
    if (mode.type === 'wizard') {
        const control = identifiable[mode.index];
        statusText.textContent = `Identifying ${mode.index + 1} of ${identifiable.length}: move ${control.name} on your board.`;
    }
    identifyBtn.textContent = mode.type === 'wizard' ? 'Stop identifying' : 'Identify controls';
    renderPicker();
}

function flash(view: ControlView) {
    view.g.classList.add('is-active');
    window.clearTimeout(view.flashTimer);
    view.flashTimer = window.setTimeout(() => view.g.classList.remove('is-active'), 160);
}

// Mirror the physical control: spin the knob's pointer, slide the slider's thumb
function animate(view: ControlView, msg: input.MidiMsg) {
    const { shape } = view.control;
    if (!view.mark) return;
    if (shape.type === 'knob') {
        // Position 0–127 sweeps the pointer from about 7 to 5 o'clock
        const angle = (msg.value / 127) * 270 - 135;
        view.mark.setAttribute('transform', `rotate(${angle} ${shape.cx} ${shape.cy})`);
    } else if (shape.type === 'slider') {
        view.mark.setAttribute('y', String(shape.top + (1 - msg.value / 127) * shape.h - 7));
    }
}

function onMidi(msg: input.MidiMsg) {
    if (!panel) return;
    const id = keyToControl.get(msg.key);
    if (id) lastKnownActivity = performance.now();
    const view = id ? views.get(id) : undefined;
    if (view) {
        flash(view);
        animate(view, msg);
    }
}

// Every message from every port, including ones the app can't bind,
// so we can see what buttons like BT and Shift actually send
function onRawMidi(data: Uint8Array, portName: string) {
    if (!panel || data[0] === 0xf8 || data[0] === 0xfe) return; // clock and keep-alive spam
    let text = `${portName} · ${input.describeRaw(data)}`;
    const msg = input.parseMidi(data, portName);
    if (msg) {
        const id = keyToControl.get(msg.key);
        text += id ? ` → ${byId.get(id)!.name}` : ' (not identified)';
        const binding = input.getBinding(msg.key);
        const action = binding ? input.getAction(binding.actionId) : undefined;
        if (action) text += ` · ${action.label}`;
    }
    monitorEl.textContent = text;

    const entry = document.createElement('li');
    entry.textContent = text;
    logEl.prepend(entry);
    while (logEl.childElementCount > maxLogEntries) logEl.lastElementChild!.remove();
}

function renderControl(control: BoardControl): ControlView {
    const g = el('g', { class: `ctl ctl-${control.kind}`, tabindex: 0, role: 'button', 'data-id': control.id });
    const title = el('title');
    g.append(title);
    let mark: SVGElement | undefined;
    const { shape } = control;

    switch (shape.type) {
        case 'knob':
            g.append(el('circle', { class: 'ctl-body', cx: shape.cx, cy: shape.cy, r: shape.r }));
            mark = el('line', { class: 'ctl-mark', x1: shape.cx, y1: shape.cy - shape.r / 3, x2: shape.cx, y2: shape.cy - shape.r + 6 });
            g.append(mark);
            break;
        case 'slider':
            // Taller invisible strip so the thin track is easy to click
            g.append(el('rect', { class: 'ctl-hit', x: shape.cx - 18, y: shape.top, width: 36, height: shape.h }));
            g.append(el('rect', { class: 'ctl-body', x: shape.cx - 6, y: shape.top, width: 12, height: shape.h, rx: 6 }));
            mark = el('rect', { class: 'ctl-thumb', x: shape.cx - 15, y: shape.top + shape.h / 2 - 7, width: 30, height: 14, rx: 3 });
            g.append(mark);
            break;
        case 'button': {
            g.append(el('rect', { class: 'ctl-body', x: shape.x, y: shape.y, width: shape.w, height: shape.h, rx: 5 }));
            const cx = shape.x + shape.w / 2;
            const cy = shape.y + shape.h / 2;
            if (shape.text) g.append(el('text', { class: 'ctl-glyph', x: cx, y: cy }, shape.text));
            if (shape.icon) g.append(el('path', { class: shape.icon.filled ? 'ctl-icon filled' : 'ctl-icon', d: shape.icon.d, transform: `translate(${cx} ${cy}) scale(${Math.min(1, shape.h / 40)})` }));
            break;
        }
    }

    let label: SVGTextElement | undefined;
    if (control.label) {
        label = el('text', { class: 'ctl-label', x: control.label.x, y: control.label.y });
        g.append(label);
    }

    const view: ControlView = { control, g, title, label, mark };
    g.addEventListener('click', () => openPicker(view));
    g.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openPicker(view);
        }
    });
    return view;
}

function buildSvg(): SVGSVGElement {
    const svg = el('svg', { class: 'board-svg', viewBox: `0 0 ${WIDTH} ${HEIGHT}`, role: 'group', 'aria-label': 'Controller layout' });
    svg.append(el('rect', { class: 'board-bg', x: 1, y: 1, width: WIDTH - 2, height: HEIGHT - 2, rx: 14 }));
    svg.append(el('line', { class: 'board-rule', x1: 16, x2: WIDTH - 16, y1: 122, y2: 122 }));
    svg.append(el('line', { class: 'board-rule', x1: 16, x2: WIDTH - 16, y1: 306, y2: 306 }));
    for (const control of layout) {
        const view = renderControl(control);
        views.set(control.id, view);
        svg.append(view.g);
    }
    return svg;
}

// ---------- Function picker ----------

function openPicker(view: ControlView) {
    if (mode.type === 'wizard') return; // clicks would fight the walkthrough
    closePicker();
    pickerControl = view.control;
    const key = profile.get(view.control.id);
    pickerReverse = key ? input.getBinding(key)?.reverse ?? false : false;
    picker = document.createElement('div');
    picker.className = 'board-picker';
    picker.setAttribute('role', 'dialog');
    picker.setAttribute('aria-label', `Bind ${view.control.name}`);
    document.body.append(picker);
    render();
    (picker.querySelector('button, input') as HTMLElement | null)?.focus();
}

function closePicker(restoreFocus = false) {
    if (!picker) return;
    if (mode.type === 'learn') input.cancelCapture();
    picker.remove();
    picker = null;
    const control = pickerControl;
    pickerControl = null;
    render();
    if (control && restoreFocus) views.get(control.id)?.g.focus();
}

function choose(control: BoardControl, actionId: string) {
    const binding: input.Binding = { actionId, reverse: pickerReverse };
    const key = profile.get(control.id);
    if (key) {
        input.setBinding(key, binding);
        closePicker();
        return;
    }
    // Not identified yet: learn which message it sends, then bind
    learn(control, { type: 'learn', controlId: control.id }, (learned) => {
        input.setBinding(learned, binding);
        closePicker();
    }, true);
}

function renderPicker() {
    if (!picker || !pickerControl) return;
    const control = pickerControl;
    const key = profile.get(control.id);
    const binding = key ? input.getBinding(key) : undefined;
    picker.replaceChildren();

    const head = document.createElement('div');
    head.className = 'board-picker-head';
    const name = document.createElement('strong');
    name.textContent = control.name;
    const sub = document.createElement('span');
    sub.textContent = control.noMidi ? 'Sends no MIDI' : key ? input.describeKey(key) : 'Not identified yet';
    head.append(name, sub);
    picker.append(head);

    if (control.noMidi) {
        const note = document.createElement('p');
        note.className = 'board-picker-hint';
        note.textContent = `${control.noMidi} The app can't see it, so it can't be bound.`;
        picker.append(note);
        positionPicker();
        return;
    }

    if (mode.type === 'learn' && mode.controlId === control.id) {
        const note = document.createElement('p');
        note.className = 'board-picker-learn';
        note.textContent = `Now move ${control.name} on your board…`;
        picker.append(note, toolButton('Cancel', () => input.cancelCapture()));
        positionPicker();
        return;
    }

    if (!key) {
        const hint = document.createElement('p');
        hint.className = 'board-picker-hint';
        hint.textContent = `Pick a function, then move ${control.name} so the app learns which one it is.`;
        picker.append(hint);
    }

    const groups = new Map<string, input.Action[]>();
    for (const action of input.getActions()) {
        if (!groups.has(action.group)) groups.set(action.group, []);
        groups.get(action.group)!.push(action);
    }
    for (const [group, actions] of groups) {
        const heading = document.createElement('div');
        heading.className = 'board-picker-group';
        heading.textContent = group;
        picker.append(heading);
        for (const action of actions) {
            const item = toolButton(action.label, () => choose(control, action.id), 'board-picker-item');
            item.setAttribute('aria-pressed', String(binding?.actionId === action.id));
            picker.append(item);
        }
    }

    const reverse = document.createElement('label');
    reverse.className = 'board-picker-reverse';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = pickerReverse;
    checkbox.addEventListener('change', () => {
        pickerReverse = checkbox.checked;
        if (key && binding) input.setBinding(key, { ...binding, reverse: pickerReverse });
    });
    reverse.append(checkbox, control.kind === 'button' ? 'Step backwards' : 'Reverse direction');
    picker.append(reverse);

    const foot = document.createElement('div');
    foot.className = 'board-picker-foot';
    if (key && binding) foot.append(toolButton('Unbind', () => input.setBinding(key, null)));
    if (key) foot.append(toolButton('Re-identify', () => learn(control, { type: 'learn', controlId: control.id }, () => { }, true)));
    if (foot.childElementCount) picker.append(foot);
    positionPicker();
}

// Below the control if it fits, otherwise above; always inside the window
function positionPicker() {
    if (!picker || !pickerControl) return;
    const anchor = views.get(pickerControl.id)!.g.getBoundingClientRect();
    const { width, height } = picker.getBoundingClientRect();
    const margin = 8;
    let top = anchor.bottom + 6;
    if (top + height > window.innerHeight - margin) top = Math.max(margin, anchor.top - height - 6);
    const left = Math.min(Math.max(margin, anchor.left), window.innerWidth - width - margin);
    picker.style.left = `${left}px`;
    picker.style.top = `${top}px`;
}

// ---------- Panel ----------

function toolButton(text: string, onClick: () => void, className = 'board-tool'): HTMLButtonElement {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = className;
    button.textContent = text;
    button.addEventListener('click', onClick);
    return button;
}

function buildPanel() {
    panel = document.createElement('section');
    panel.className = 'board-panel hidden';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'MIDI controller');

    const head = document.createElement('header');
    head.className = 'board-panel-head';
    const title = document.createElement('span');
    title.className = 'board-panel-title';
    title.textContent = 'Controller';
    const hint = document.createElement('span');
    hint.className = 'board-panel-hint';
    hint.textContent = 'Click a control to choose what it does';
    const tools = document.createElement('div');
    tools.className = 'board-panel-tools';
    identifyBtn = toolButton('Identify controls', () => {
        if (mode.type === 'wizard') input.cancelCapture();
        else startWizard();
    });
    identifyBtn.title = 'Move each control in turn so the app learns what it sends';
    const copyBtn = toolButton('Copy map', copyMap);
    copyBtn.title = 'Copy which MIDI message each control sends';
    minimizeBtn = toolButton('–', toggleMinimized, 'board-tool board-icon-btn');
    minimizeBtn.setAttribute('aria-label', 'Minimize');
    const closeBtn = toolButton('×', closeBoardPanel, 'board-tool board-icon-btn');
    closeBtn.setAttribute('aria-label', 'Close');
    tools.append(identifyBtn, copyBtn, minimizeBtn, closeBtn);
    head.append(title, hint, tools);

    const body = document.createElement('div');
    body.className = 'board-panel-body';
    body.append(buildSvg());

    const foot = document.createElement('footer');
    foot.className = 'board-panel-foot';
    statusEl = document.createElement('div');
    statusEl.className = 'board-status hidden';
    statusText = document.createElement('span');
    const skipBtn = toolButton('Skip', () => {
        if (mode.type === 'wizard') wizardStep(mode.index + 1);
    });
    statusEl.append(statusText, skipBtn, toolButton('Stop', () => input.cancelCapture()));
    monitorEl = document.createElement('div');
    monitorEl.className = 'board-monitor';
    monitorEl.textContent = 'Move any control on your board to see the message it sends.';
    logBtn = toolButton('Log', () => {
        const open = logEl.classList.toggle('hidden') === false;
        logBtn.setAttribute('aria-expanded', String(open));
    });
    logBtn.title = 'Show the last messages from every MIDI port';
    logBtn.setAttribute('aria-expanded', 'false');
    const monitorRow = document.createElement('div');
    monitorRow.className = 'board-monitor-row';
    monitorRow.append(monitorEl, logBtn);
    logEl = document.createElement('ol');
    logEl.className = 'board-log hidden';
    logEl.setAttribute('aria-label', 'MIDI message log');
    foot.append(statusEl, monitorRow, logEl);

    panel.append(head, body, foot);
    document.body.append(panel);
    makeDraggable(head);
}

function makeDraggable(handle: HTMLElement) {
    let startX = 0, startY = 0, startLeft = 0, startTop = 0;
    handle.addEventListener('pointerdown', (e) => {
        if ((e.target as HTMLElement).closest('button') || e.button !== 0) return;
        startX = e.clientX;
        startY = e.clientY;
        startLeft = panel.offsetLeft;
        startTop = panel.offsetTop;
        handle.setPointerCapture(e.pointerId);
        panel.classList.add('is-dragging');
    });
    handle.addEventListener('pointermove', (e) => {
        if (!handle.hasPointerCapture(e.pointerId)) return;
        moveTo(startLeft + e.clientX - startX, startTop + e.clientY - startY);
    });
    const end = (e: PointerEvent) => {
        if (handle.hasPointerCapture(e.pointerId)) handle.releasePointerCapture(e.pointerId);
        panel.classList.remove('is-dragging');
    };
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
}

// Keep at least the title bar reachable
function moveTo(left: number, top: number) {
    const keep = 80;
    left = Math.min(Math.max(left, keep - panel.offsetWidth), window.innerWidth - keep);
    top = Math.min(Math.max(top, 0), window.innerHeight - 40);
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
}

function toggleMinimized() {
    const minimized = !panel.classList.contains('is-min');
    if (minimized) {
        savedHeight = panel.style.height;
        panel.style.height = '';
        closePicker();
    } else {
        panel.style.height = savedHeight;
    }
    panel.classList.toggle('is-min', minimized);
    minimizeBtn.textContent = minimized ? '+' : '–';
    minimizeBtn.setAttribute('aria-label', minimized ? 'Expand' : 'Minimize');
}

function copyMap() {
    const json = JSON.stringify(Object.fromEntries(profile), null, 2);
    console.log(json);
    navigator.clipboard.writeText(json)
        .then(() => toast.newMessage(`Control map copied (${profile.size} of ${identifiable.length} identified)`))
        .catch(() => toast.newMessage('Could not copy; the map is printed in the console'));
}

export function openBoardPanel() {
    panel.classList.remove('hidden');
    if (panel.classList.contains('is-min')) toggleMinimized();
    if (!placed) {
        placed = true;
        const width = Math.min(820, window.innerWidth - 32);
        panel.style.width = `${width}px`;
        const topbar = document.querySelector('.topbar')?.getBoundingClientRect();
        moveTo(window.innerWidth - width - 16, (topbar?.bottom ?? 0) + 12);
    }
}

export function closeBoardPanel() {
    closePicker();
    if (mode.type !== 'idle') input.cancelCapture();
    panel.classList.add('hidden');
}

export function initBoard() {
    buildPanel();
    loadProfile();
    input.onChange(render);
    input.onMessage(onMidi);
    midi.addMidiEventListener(onRawMidi);
    render();

    document.getElementById('knobs-btn')?.addEventListener('click', openBoardPanel);

    // Close the picker on a click outside it (a click on another control reopens it there)
    document.addEventListener('pointerdown', (e) => {
        if (picker && !picker.contains(e.target as Node)) closePicker();
    }, true);
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        if (picker) closePicker(true);
        else if (mode.type !== 'idle') input.cancelCapture();
    });
    window.addEventListener('resize', () => {
        if (placed) moveTo(panel.offsetLeft, panel.offsetTop);
        positionPicker();
    });
}
