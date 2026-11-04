import * as parser from './file';
import * as renderer from './render';
import * as input from './inputManager';
import * as toast from './toast';
import { chooseOption } from './header';

// Result switcher for batch-results CSVs: many reductions of the same points,
// one block of rows per run. Shows one run at a time and lets the method and
// each parameter be picked (or turned on a MIDI knob) to swap to another run.
//
// Two layouts are understood:
//   wide (backend batch export): index, method, label, dim1..N, <method>_<param>…
//        (to_wide_format_csv.py's param_<name> columns work too)
//   long: index, label, method, param_name, param_value, dim1, dim2

interface Run {
    method: string;
    params: Map<string, string>; // parameter name -> value as text, e.g. "n_neighbors" -> "15"
    rows: number[]; // point indices
}

export interface RunState {
    method: string;
    params: Record<string, string>;
}

let runs: Run[] = [];
let current: Run | null = null;

const container = document.getElementById('runs-container') as HTMLDivElement;
const runLabel = document.getElementById('run-label') as HTMLSpanElement;
const summary = document.getElementById('runs-summary') as HTMLParagraphElement;
const pickers = document.getElementById('run-pickers') as HTMLDivElement;
const stepAllBtn = document.getElementById('run-step-bind') as HTMLButtonElement;

// "10.0" and "10" are the same setting
function formatValue(text: string): string {
    const n = parseFloat(text);
    return isNaN(n) ? text.trim() : String(n);
}

function compareValues(a: string, b: string): number {
    const na = parseFloat(a), nb = parseFloat(b);
    if (!isNaN(na) && !isNaN(nb)) return na - nb;
    return a.localeCompare(b, undefined, { numeric: true });
}

export function describeRun(run: { method: string; params: Map<string, string> | Record<string, string> }): string {
    const entries = run.params instanceof Map ? Array.from(run.params) : Object.entries(run.params);
    return [run.method, ...entries.map(([name, value]) => `${name} ${value}`)].join(' · ');
}

// Group the rows into runs, or return [] if this isn't a batch-results file
function findRuns(): Run[] {
    const cols = parser.columns;
    const col = (name: string) => cols.indexOf(name);
    if (col('method') < 0 || col('dim1') < 0 || col('dim2') < 0) return [];

    const methodIdx = col('method');
    const nameIdx = col('param_name');
    const valueIdx = col('param_value');
    const long = nameIdx >= 0 && valueIdx >= 0;

    const methods = new Set(parser.rows.slice(1).map((row) => row[methodIdx]));
    // Wide parameter columns: "<method>_<param>" belongs to that method, "param_<name>" to all of them
    const paramCols = long ? [] : cols.flatMap((name, idx) => {
        if (name.startsWith('param_')) return [{ idx, method: null as string | null, name: name.slice('param_'.length) }];
        for (const method of methods) {
            if (name.startsWith(`${method}_`)) return [{ idx, method, name: name.slice(method.length + 1) }];
        }
        return [];
    });

    const byKey = new Map<string, Run>();
    for (let i = 1; i < parser.rows.length; i++) {
        const row = parser.rows[i];
        const method = row[methodIdx];
        const params: [string, string][] = [];
        if (long) {
            if (row[nameIdx] !== '') params.push([row[nameIdx], formatValue(row[valueIdx])]);
        } else {
            for (const pc of paramCols) {
                if (pc.method !== null && pc.method !== method) continue;
                if (row[pc.idx] === undefined || row[pc.idx] === '') continue;
                params.push([pc.name, formatValue(row[pc.idx])]);
            }
        }
        const key = `${method}\u0000${params.map((p) => p.join('=')).join('&')}`;
        let run = byKey.get(key);
        if (!run) {
            run = { method, params: new Map(params), rows: [] };
            byKey.set(key, run);
        }
        run.rows.push(i - 1);
    }

    return Array.from(byKey.values()).sort((a, b) => {
        if (a.method !== b.method) return a.method.localeCompare(b.method);
        const names = Array.from(new Set([...a.params.keys(), ...b.params.keys()])).sort();
        for (const name of names) {
            const va = a.params.get(name), vb = b.params.get(name);
            if (va === vb) continue;
            if (va === undefined) return -1;
            if (vb === undefined) return 1;
            return compareValues(va, vb);
        }
        return 0;
    });
}

// Of the candidates, the one sharing the most settings with `params`; ties go to the earliest
function bestMatch(candidates: Run[], params: Map<string, string>): Run | undefined {
    let best: Run | undefined;
    let bestScore = -1;
    for (const run of candidates) {
        let score = 0;
        for (const [name, value] of params) if (run.params.get(name) === value) score++;
        if (score > bestScore) {
            best = run;
            bestScore = score;
        }
    }
    return best;
}

function methods(): string[] {
    return Array.from(new Set(runs.map((run) => run.method)));
}

function paramNames(method: string): string[] {
    const names = new Set<string>();
    for (const run of runs) if (run.method === method) run.params.forEach((_, name) => names.add(name));
    return Array.from(names);
}

function paramValues(method: string, name: string): string[] {
    const values = new Set<string>();
    for (const run of runs) {
        const value = run.params.get(name);
        if (run.method === method && value !== undefined) values.add(value);
    }
    return Array.from(values).sort(compareValues);
}

function selectRun(run: Run, announce = true) {
    current = run;
    const mask = new Uint8Array(renderer.pointCount());
    for (const i of run.rows) mask[i] = 1;
    renderer.setVisibilityLayer('run', mask);

    // Plot the run's coordinates. Changing an axis frames it; refit anyway since
    // the axes may already have been dim1/dim2 for the previous run.
    const x = document.getElementById('select-xaxis') as HTMLSelectElement;
    const y = document.getElementById('select-yaxis') as HTMLSelectElement;
    const z = document.getElementById('select-zaxis') as HTMLSelectElement;
    if (x.value !== 'dim1') chooseOption(x, 'dim1');
    if (y.value !== 'dim2') chooseOption(y, 'dim2');
    if (parser.columns.includes('dim3') && z.value !== 'dim3') chooseOption(z, 'dim3');
    renderer.fitToVisible();

    runLabel.textContent = describeRun(run);
    renderPickers();
    if (announce) toast.newMessage(`${describeRun(run)} (${run.rows.length.toLocaleString()} points)`);
}

function pickMethod(method: string) {
    const run = bestMatch(runs.filter((r) => r.method === method), current?.params ?? new Map());
    if (run) selectRun(run);
}

function pickParam(name: string, value: string) {
    if (!current) return;
    const method = current.method;
    const run = bestMatch(runs.filter((r) => r.method === method && r.params.get(name) === value), current.params);
    if (run) selectRun(run);
}

// Knob turns move through the values in order and stop at the ends
function stepParam(name: string, step: input.Step) {
    if (!current) return;
    const values = paramValues(current.method, name);
    if (values.length === 0) {
        toast.newMessage(`${current.method} has no ${name} setting`);
        return;
    }
    const at = values.indexOf(current.params.get(name) ?? '');
    const next = at < 0 ? (step > 0 ? 0 : values.length - 1) : Math.min(values.length - 1, Math.max(0, at + step));
    if (next !== at) pickParam(name, values[next]);
}

function pickerRow(label: string, options: { value: string; text: string }[], selected: string, actionId: string, onPick: (value: string) => void) {
    const row = document.createElement('div');
    row.className = 'picker-row';
    const text = document.createElement('label');
    text.className = 'text';
    text.textContent = label;
    const select = document.createElement('select');
    for (const { value, text: optionText } of options) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = optionText;
        select.appendChild(option);
    }
    if (!options.some((o) => o.value === selected)) {
        // This run doesn't use the setting (e.g. a sweep that only varied another one)
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'default';
        option.disabled = true;
        select.prepend(option);
    }
    select.value = selected;
    select.onchange = () => onPick(select.value);
    const bind = document.createElement('button');
    bind.type = 'button';
    bind.className = 'bind-btn';
    row.append(text, select, bind);
    pickers.appendChild(row);
    input.attachBindButton(bind, actionId);
}

function renderPickers() {
    pickers.replaceChildren();
    if (!current) return;
    const methodList = methods();
    pickerRow('Method', methodList.map((m) => ({ value: m, text: m })), current.method, 'run.method', pickMethod);
    for (const name of paramNames(current.method)) {
        const values = paramValues(current.method, name);
        pickerRow(name, values.map((v) => ({ value: v, text: v })), current.params.get(name) ?? '', `run.param.${name}`, (v) => pickParam(name, v));
    }
}

// Call after setupHeader for each newly loaded file
export function initRuns() {
    for (const action of input.getActions()) {
        if (action.group === 'Runs') input.unregisterAction(action.id);
    }
    runs = findRuns();
    current = null;
    pickers.replaceChildren();
    if (runs.length === 0) {
        container.classList.add('hidden');
        return;
    }
    container.classList.remove('hidden');

    const methodList = methods();
    summary.textContent = `${runs.length} runs · ${methodList.join(', ')}. Each change shows one run's points.`;

    input.registerAction({
        id: 'run.method', label: 'Run: method', group: 'Runs',
        run: (step) => {
            if (!current) return;
            const list = methods();
            pickMethod(list[(list.indexOf(current.method) + step + list.length) % list.length]);
        },
    });
    const allNames = new Set(methodList.flatMap(paramNames));
    for (const name of allNames) {
        input.registerAction({ id: `run.param.${name}`, label: `Run: ${name}`, group: 'Runs', run: (step) => stepParam(name, step) });
    }
    input.registerAction({
        id: 'run.step', label: 'Run: step through all', group: 'Runs',
        run: (step) => {
            if (!current) return;
            const at = runs.indexOf(current);
            const next = Math.min(runs.length - 1, Math.max(0, at + step));
            if (next !== at) selectRun(runs[next]);
        },
    });
    input.attachBindButton(stepAllBtn, 'run.step');

    selectRun(runs[0], false);
    // Color by the class label when there is one, so runs can be compared at a glance
    const color = document.getElementById('color-select') as HTMLSelectElement;
    if (parser.columns.includes('label')) chooseOption(color, 'label');
    toast.newMessage(`Loaded ${runs.length} runs. Showing ${describeRun(runs[0])}`);
}

export function getRunState(): RunState | null {
    return current ? { method: current.method, params: Object.fromEntries(current.params) } : null;
}

export function applyRunState(state: RunState) {
    const params = new Map(Object.entries(state.params));
    const run = bestMatch(runs.filter((r) => r.method === state.method), params);
    if (run) selectRun(run, false);
}
