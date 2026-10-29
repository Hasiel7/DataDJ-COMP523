import * as renderer from './render';
import * as toast from './toast';
import * as parser from './file';
import { Lut } from 'three/addons/math/Lut.js';
import * as input from './inputManager';
import * as d3 from 'd3';

const maxColorsOnScreen = 10;
// "a human can typically distinguish between 6 to 10 distinct colors" -Internet/Gemini
// function createColorMap(data: Float32Array, selector: string): (v: number) => string {
//     const min = d3.min(data) ?? 0;
//     const max = d3.max(data) ?? 1;

//     document.querySelector(selector)!.classList.remove('hidden')

//     // 1. Create the Sequential Scale
//     // Using interpolateTurbo to match the high-contrast rainbow in your image
//     const colorScale = d3.scaleSequential<string>()
//         .domain([min, max])
//         .interpolator(d3.interpolateTurbo)
//         .clamp(true);

//     // 2. Setup Dimensions for Legend
//     const width = 100;
//     const height = 300;
//     const margin = { top: 20, right: 50, bottom: 20, left: 10 };

//     const svg = d3.select(selector)
//         .append("svg")
//         .attr("width", width + margin.left + margin.right)
//         .attr("height", height + margin.top + margin.bottom)
//         .append("g")
//         .attr("transform", `translate(${margin.left},${margin.top})`);

//     // 3. Define the Gradient
//     const defs = svg.append("defs");
//     const linearGradient = defs.append("linearGradient")
//         .attr("id", "legend-gradient")
//         .attr("x1", "0%").attr("y1", "100%") // Bottom (min)
//         .attr("x2", "0%").attr("y2", "0%");   // Top (max)

//     // Add color stops (every 10% for a smooth transition)
//     const stops = 10;
//     d3.range(stops).forEach(i => {
//         const offset = i / (stops - 1);
//         linearGradient.append("stop")
//             .attr("offset", `${offset * 100}%`)
//             .attr("stop-color", colorScale(min + (max - min) * offset));
//     });

//     // 4. Draw the Legend Rectangle
//     svg.append("rect")
//         .attr("width", 20)
//         .attr("height", height)
//         .style("fill", "url(#legend-gradient)");

//     // 5. Add the Axis
//     const axisScale = d3.scaleLinear()
//         .domain([min, max])
//         .range([height, 0]);

//     const axis = d3.axisRight(axisScale)
//         .ticks(5)
//         .tickFormat(d3.format(".2f"));

//     svg.append("g")
//         .attr("transform", `translate(20, 0)`)
//         .call(axis);

//     return colorScale;
// }

function createSelOption(parent: HTMLSelectElement, colName: string) {
    const el = document.createElement("option") as HTMLOptionElement;
    el.value = el.textContent = colName;
    parent.appendChild(el)
}

function rotateSelectOption(selectElement: HTMLSelectElement, clockwise: boolean) {
    if (!selectElement || selectElement.options.length <= 1) return;

    const totalOptions = selectElement.options.length;
    const currentIndex = selectElement.selectedIndex;

    // Calculate new index: if not clockwise, go backwards (+ total for positive modulus)
    let nextIndex = clockwise ? (currentIndex + 1) : (currentIndex - 1 + totalOptions);

    // Use modulo operator to loop around
    selectElement.selectedIndex = nextIndex % totalOptions;

    // Optional: Trigger change event so other code knows it changed
    selectElement.dispatchEvent(new Event('change'));
}

// Called once at startup; calling it again would add a second toggle
// listener and each click would open and immediately close the dropdown.
export function initDropdowns() {
    // const dropdown = document.getElementById('filter-dropdown') as HTMLDivElement;
    document.querySelectorAll(".dropdown-container").forEach((container) => {
        const dropdown = container.querySelector(".dropdown") as HTMLDivElement
        const toggleBtn = container.querySelector('.main-btn') as HTMLButtonElement;
        // Toggle Dropdown Visibility
        toggleBtn.addEventListener('click', () => {
            dropdown.classList.toggle('hidden');
        });

        // Close dropdown if clicking outside
        document.addEventListener('mousedown', (event) => {
            const isClickInside = dropdown.contains(event.target as Node) || toggleBtn.contains(event.target as Node);
            if (!isClickInside) {
                dropdown.classList.add('hidden');
            }
        });
    })
    initSettingsDropdown()
    initColumnCycleActions()
}

function initSettingsDropdown() {
    const bindings: { text: string; label: string; key: string }[] = [
        { text: 'XMIN', label: 'Left edge', key: 'xmin' },
        { text: 'XMAX', label: 'Right edge', key: 'xmax' },
        { text: 'YMIN', label: 'Bottom edge', key: 'ymin' },
        { text: 'YMAX', label: 'Top edge', key: 'ymax' },
        { text: 'Point Size', label: 'Point size', key: 'size' },
        { text: 'Zoom', label: 'Zoom', key: 'zoom' },
        { text: 'Alpha', label: 'Opacity', key: 'alpha' },
    ];
    // Only the Knobs menu: rows are matched to `bindings` by position
    const rows = document.querySelectorAll("#knobs-btn + .dropdown .container-row");
    rows.forEach((row, i) => {
        const bindBtn = row.querySelector('.bind-btn') as HTMLButtonElement;
        const { text, label, key } = bindings[i];
        let run: (step: input.Step) => void = () => { };
        const speed = 1.0;

        switch (key) {
            case "xmin":
                run = (step) => {
                    renderer.moveCameraLeft(step * speed)
                    renderer.camera.updateProjectionMatrix();
                    toast.newMessage(`${text} Changed`)
                }
                break;
            case "xmax":
                run = (step) => {
                    renderer.moveCameraRight(step * speed);
                    renderer.camera.updateProjectionMatrix();
                    toast.newMessage(`${text} Changed`)
                }
                break;
            case "ymin":
                run = (step) => {
                    // In WebGL, standard Y grows upwards.
                    // ymin typically corresponds to the bottom edge.
                    renderer.moveCameraDown(step * speed);
                    renderer.camera.updateProjectionMatrix();
                    toast.newMessage(`${text} Changed`)
                }
                break;
            case "ymax":
                run = (step) => {
                    // ymax typically corresponds to the top edge.
                    renderer.moveCameraUp(step * speed);
                    renderer.camera.updateProjectionMatrix();
                    toast.newMessage(`${text} Changed`)
                }
                break;

            case "size":
                run = (step) => {
                    renderer.changePointSize(renderer.pointsMaterial.size + step)
                    toast.newMessage(`${text} Changed`)
                }
                break;

            case "zoom":
                run = (step) => {
                    const camera = renderer.camera;
                    if (!camera) return;

                    // Clockwise = Zoom In, Counter-Clockwise = Zoom Out.
                    // Multiply rather than add, so each click feels the same at 0.1x and at 100x.
                    // Never let zoom hit 0 or go negative, it will break the WebGL projection!
                    camera.zoom = Math.min(1000, Math.max(0.05, camera.zoom * Math.pow(1.15, step)));

                    // Tell Three.js to apply the math
                    camera.updateProjectionMatrix();

                    toast.newMessage(`${text} Changed: ${camera.zoom.toPrecision(3)}x`);
                }
                break;

            case "alpha":
                run = (step) => {
                    const speed = 0.1; //Should let a knob control this
                    const nAlpha = renderer.getPointsAlpha() + step * speed;
                    renderer.changeTransparency(Math.min(1, Math.max(speed, nAlpha)));
                }
                break;
        }

        const id = `view.${key}`
        input.registerAction({ id, label, group: 'View', run })
        input.attachBindButton(bindBtn, id)
    })
}

// Filter rows get a fresh action id each, so a deleted row's binding never leaks into a new one
let filterCount = 0;

// An "=" filter lists every value; past this many, only range comparisons are offered
const maxFilterOptions = 500;

export type FilterOp = '=' | '>' | '>=' | '<' | '<=';

export interface FilterState {
    column: string;
    op: FilterOp;
    value: string; // '' = no value yet, so the filter lets everything through
}

// Everything about the view that a favorite saves, apart from the result-switcher run
export interface ViewState {
    dimension: string;
    x: string;
    y: string;
    z: string;
    color: string;
    filters: FilterState[];
    hiddenCategories: string[]; // legend entries clicked off
}

// Set by setupHeader for the loaded file
let addFilterRow: (init?: FilterState) => void = () => { };
let applyFilters: () => void = () => { };
let applyLegend: () => void = () => { };
// Labels of the legend categories that are hidden
let legendHidden = new Set<string>();

function initColumnCycleActions() {
    const cycles: { id: string; label: string; select: string; button: string; message: string }[] = [
        { id: 'column.x', label: 'X column', select: 'select-xaxis', button: 'x-cycle', message: 'X-Axis Changed to' },
        { id: 'column.y', label: 'Y column', select: 'select-yaxis', button: 'y-cycle', message: 'Y-Axis Changed to' },
        { id: 'column.z', label: 'Z column', select: 'select-zaxis', button: 'z-cycle', message: 'Z-Axis Changed to' },
        { id: 'column.color', label: 'Color column', select: 'color-select', button: 'color-cycle', message: 'Coloring Changed to' },
    ];
    for (const { id, label, select, button, message } of cycles) {
        const selectEl = document.getElementById(select) as HTMLSelectElement
        input.registerAction({
            id, label, group: 'Columns',
            run: (step) => {
                if (selectEl.disabled) return;
                rotateSelectOption(selectEl, step === 1);
                toast.newMessage(`${message} ${selectEl.value}`)
            },
        })
        input.attachBindButton(document.getElementById(button) as HTMLButtonElement, id)
    }
}

// Test for one filter row, or null when it doesn't filter anything yet
function filterTest({ column, op, value }: FilterState): ((v: number) => boolean) | null {
    if (value === '' || !parser.parsedData.has(column)) return null;
    if (op === '=') {
        // parsedData holds numbers as float32, and strings as their id
        const target = parser.colTypes.get(column) === 'string'
            ? parser.strColMap.get(column)?.get(value)
            : Math.fround(parseFloat(value));
        if (target === undefined) return () => false;
        return (v) => v === target;
    }
    const t = parseFloat(value);
    if (isNaN(t)) return null;
    switch (op) {
        case '>': return (v) => v > t;
        case '>=': return (v) => v >= t;
        case '<': return (v) => v < t;
        case '<=': return (v) => v <= t;
    }
}

function readFilterRow(row: Element): FilterState {
    const op = (row.querySelector('.filter-op') as HTMLSelectElement).value as FilterOp;
    return {
        column: (row.querySelector('.filter-field') as HTMLSelectElement).value,
        op,
        value: op === '='
            ? (row.querySelector('.filter-value') as HTMLSelectElement).value
            : (row.querySelector('.filter-number') as HTMLInputElement).value,
    };
}

function setupHeader(data: string[][]) {
    renderer.clearPoints()
    parser.parseData(data)

    const cols: string[] = parser.columns // blank header cells already renamed
    // TODO: use global data variable

    renderer.initDrawData(data.length - 1) // data[0] is the header row, not a point

    const addFilterBtn = document.getElementById('add-filter-row') as HTMLButtonElement;
    const filterList = document.getElementById('filter-list') as HTMLDivElement;
    // Filters from a previous file refer to columns that may no longer exist
    filterList.replaceChildren()
    for (const action of input.getActions()) {
        if (action.group === 'Filters') input.unregisterAction(action.id)
    }
    addFilterBtn.disabled = false

    applyFilters = () => {
        const tests: { col: Float32Array; test: (v: number) => boolean }[] = []
        for (const filterRow of filterList.children) {
            const filter = readFilterRow(filterRow)
            const test = filterTest(filter)
            if (test) tests.push({ col: parser.parsedData.get(filter.column)!, test })
        }
        if (tests.length === 0) {
            renderer.setVisibilityLayer('filters', null)
            return
        }
        // One entry per point. Point i is CSV row i + 1 (row 0 is the header).
        const rowMask = new Uint8Array(data.length - 1)
        for (let i = 0; i < rowMask.length; i++) {
            let flag = 1;
            for (const { col, test } of tests) {
                if (!test(col[i])) { flag = 0; break; }
            }
            rowMask[i] = flag;
        }
        renderer.setVisibilityLayer('filters', rowMask)
    }

    // Function to create a new filter row
    function createFilterRow(init?: FilterState) {
        const row = document.createElement('div');
        row.className = 'filter-row';

        row.innerHTML = `
        <select class="filter-field" title="Column">
        </select>
        <select class="filter-op" title="Comparison">
            <option value="=">=</option>
            <option value=">">&gt;</option>
            <option value=">=">≥</option>
            <option value="<">&lt;</option>
            <option value="<=">≤</option>
        </select>
        <select class="filter-value" title="Value">
        </select>
        <input class="filter-number hidden" type="number" step="any" title="Threshold">
        <button class="bind-btn">Bind</button>
        <button class="delete-btn">×</button>
        `;
        const rowSel = row.querySelector('.filter-field') as HTMLSelectElement
        const opSel = row.querySelector('.filter-op') as HTMLSelectElement
        const rowValSel = row.querySelector('.filter-value') as HTMLSelectElement
        const numInput = row.querySelector('.filter-number') as HTMLInputElement
        cols.forEach((colName) => createSelOption(rowSel, colName))
        const bindBtn = row.querySelector('.bind-btn') as HTMLButtonElement;

        // How far one knob click moves a threshold
        let knobStep = 1;

        const showOp = () => {
            const isRange = opSel.value !== '=';
            rowValSel.classList.toggle('hidden', isRange);
            numInput.classList.toggle('hidden', !isRange);
        }

        // Refill the value list and range hints for the chosen column
        const fillValues = () => {
            const colName = rowSel.value
            const vals = parser.strColMap.get(colName)
            if (vals === undefined) {
                throw new Error(`Column ${colName} is not properly parsed (Col map is undefined).`)
            }
            const isNum = parser.colTypes.get(colName) === 'number'
            const tooMany = isNum && vals.size > maxFilterOptions
            // Comparisons only make sense for numbers; a list of thousands of exact values doesn't help anyone
            for (const opt of opSel.options) opt.disabled = opt.value === '=' ? tooMany : !isNum
            if (opSel.selectedOptions[0]?.disabled) opSel.value = isNum ? '>=' : '='

            rowValSel.replaceChildren()
            const any = document.createElement('option')
            any.value = ''
            any.textContent = 'Any'
            rowValSel.appendChild(any)
            if (!tooMany) {
                const sorted = Array.from(vals.keys()).sort(isNum
                    ? (a, b) => (a as number) - (b as number)
                    : (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true }))
                sorted.slice(0, maxFilterOptions).forEach((val) => createSelOption(rowValSel, String(val)))
            }

            numInput.value = ''
            if (isNum) {
                const [min, max] = d3.extent(parser.parsedData.get(colName)!) as [number, number]
                const fmt = d3.format('.4~g')
                numInput.placeholder = `${fmt(min)} – ${fmt(max)}`
                knobStep = min < max ? d3.tickStep(min, max, 50) : 1
            }
            showOp()
        }

        const actionId = `filter.${++filterCount}`
        const registerFilterAction = () => input.registerAction({
            id: actionId,
            label: rowSel.value ? `Filter: ${rowSel.value}` : 'Filter value',
            group: 'Filters',
            run: (step) => {
                if (opSel.value === '=') {
                    rotateSelectOption(rowValSel, step === 1)
                    return
                }
                // A knob nudges the threshold, starting from the middle of the column's range
                const [min, max] = d3.extent(parser.parsedData.get(rowSel.value)!) as [number, number]
                const current = parseFloat(numInput.value)
                const next = (isNaN(current) ? (min + max) / 2 : current) + step * knobStep
                numInput.value = next.toFixed(d3.precisionFixed(knobStep))
                applyFilters()
                toast.newMessage(`${rowSel.value} ${opSel.selectedOptions[0].textContent} ${numInput.value}`)
            },
        })

        fillValues()
        if (init && cols.includes(init.column)) {
            rowSel.value = init.column
            fillValues()
            opSel.value = init.op
            if (init.op === '=') {
                // The saved value might be past the option cap; add it so it can be selected
                if (!Array.from(rowValSel.options).some((o) => o.value === init.value)) createSelOption(rowValSel, init.value)
                rowValSel.value = init.value
            }
            else numInput.value = init.value
            showOp()
        }
        registerFilterAction()
        input.attachBindButton(bindBtn, actionId);

        rowSel.addEventListener('change', () => {
            fillValues()
            registerFilterAction()
            applyFilters()
            toast.newMessage(`Filtering on ${rowSel.value}`)
        })

        opSel.addEventListener('change', () => {
            // Carry an exact value over as the starting threshold
            if (opSel.value !== '=' && numInput.value === '' && rowValSel.value !== '') numInput.value = rowValSel.value
            showOp()
            applyFilters()
        })

        rowValSel.addEventListener('change', () => {
            const colName = rowSel.value
            toast.newMessage(`${colName}: ${rowValSel.value || 'Any'}`)
            applyFilters()
        });

        numInput.addEventListener('input', applyFilters)

        // Handle Delete logic
        row.querySelector('.delete-btn')?.addEventListener('click', () => {
            row.remove();
            input.unregisterAction(actionId)
            applyFilters()
        });

        filterList.appendChild(row);
    }

    // Assigned rather than added so loading another file doesn't stack handlers
    addFilterBtn.onclick = () => createFilterRow();
    addFilterRow = createFilterRow;

    // Color Select
    const colorSelect = document.getElementById('color-select') as HTMLSelectElement
    colorSelect.options.length = 0;
    const legend = document.querySelector('.legend') as HTMLDivElement;
    const legendTitle = legend.querySelector('.legend-title') as HTMLDivElement
    const legendContent = legend.querySelector('.legend-content') as HTMLDivElement
    legend.classList.add('hidden') // initDrawData already dropped the old file's legend layer

    // onchange (not addEventListener) so loading another file replaces the handler
    colorSelect.onchange = () => {
        const colName = colorSelect.value
        // A new coloring starts with every category shown
        legendHidden = new Set()
        applyLegend = () => { }
        renderer.setVisibilityLayer('legend', null)
        if (colName === '' || !parser.parsedData.has(colName)) {
            // reset colors
            renderer.resetPointColors()
            legend.classList.add('hidden')
            return
        }
        toast.newMessage(`Coloring based off ${colName}`)
        legendTitle.textContent = colName;
        const columnList = parser.parsedData.get(colName) as Float32Array

        const [min, max] = d3.extent(columnList) as [number, number];

        const uniqueCount = parser.uniques.get(colName) as number

        const rowMask = new Float32Array(columnList.length)

        if (uniqueCount > maxColorsOnScreen) { //color continuously
            legend.classList.remove('hidden')
            legend.classList.add('continuous')
            const colorScale = d3.scaleSequential(d3.interpolatePlasma).domain([min, max]);
            const previewHtml = d3.ticks(min, max, 14)
                .map(t => colorScale(t))
                .map(color => `
                    <span style="
                        background: ${color};
                        height: 100%;
                        flex: 1;
                        display: inline-block;
                    ">&nbsp;</span>
                    `).join('');

            legendContent.innerHTML = `
                <div class="legend-gradient">${previewHtml}</div>
                <div class="legend-range"><span>${d3.format('.3~g')(min)}</span><span>${d3.format('.3~g')(max)}</span></div>
                `;

            renderer.colorMapRowsContinuous(columnList, colorScale);
            return;
        } //end continuous
        legend.classList.remove('continuous')

        // Sort the categories (numbers numerically, strings alphabetically) and
        // color each row by its category's position in that order. Coloring by
        // the raw value would bunch 0 and 10 together next to 100, and the
        // legend labels wouldn't match.
        const isDiscreteCol = parser.colTypes.get(colName) === 'string';
        const categories: { key: number; label: string }[] = isDiscreteCol
            ? Array.from(parser.invertedStrMap.get(colName)!, ([id, label]) => ({ key: id, label }))
                .sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }))
            : Array.from(parser.strColMap.get(colName)!.keys() as Iterable<number>)
                .sort((a, b) => a - b)
                // columnList is a Float32Array, so match on the float32 value
                .map((value) => ({ key: Math.fround(value), label: String(value) }));
        const numSteps = categories.length;
        const rankOf = new Map(categories.map((c, rank) => [c.key, rank]));
        const toAlpha = (rank: number) => numSteps > 1 ? rank / (numSteps - 1) : 0;

        for (let i = 0; i < columnList.length; i++) {
            rowMask[i] = toAlpha(rankOf.get(columnList[i]) ?? 0)
        }

        // TODO: Allow user to choose colormap argument for Lut (currently rainbow)
        const colorMap = new Lut("rainbow", numSteps)

        renderer.colorMapRowsDiscrete(rowMask, colorMap)

        //Update legend. Each entry is a button that hides or shows its category.
        legend.classList.remove('hidden');
        legendContent.replaceChildren()
        const items = categories.map(({ label }, rank) => {
            const item = document.createElement('button')
            item.type = 'button'
            item.className = 'legend-item'
            item.title = 'Click to hide or show'
            const swatch = document.createElement('span')
            swatch.className = 'legend-color'
            swatch.style.backgroundColor = `#${colorMap.getColor(toAlpha(rank)).getHexString()}`
            const text = document.createElement('span')
            text.className = 'legend-label'
            text.textContent = label
            item.append(swatch, text)
            item.onclick = () => {
                if (legendHidden.has(label)) legendHidden.delete(label)
                else legendHidden.add(label)
                applyLegend()
            }
            legendContent.appendChild(item)
            return item
        })

        applyLegend = () => {
            categories.forEach(({ label }, rank) => {
                const off = legendHidden.has(label)
                items[rank].classList.toggle('is-off', off)
                items[rank].setAttribute('aria-pressed', String(!off))
            })
            if (legendHidden.size === 0) {
                renderer.setVisibilityLayer('legend', null)
                return
            }
            const hiddenRanks = new Set(categories.flatMap(({ label }, rank) => legendHidden.has(label) ? [rank] : []))
            const mask = new Uint8Array(columnList.length)
            for (let i = 0; i < columnList.length; i++) {
                mask[i] = hiddenRanks.has(rankOf.get(columnList[i]) ?? 0) ? 0 : 1
            }
            renderer.setVisibilityLayer('legend', mask)
        }
        applyLegend()
    }

    // Axes
    const selectElX = document.getElementById('select-xaxis') as HTMLSelectElement
    selectElX.options.length = 0;
    selectElX.onchange = () => {
        const colName = selectElX.value
        if (colName === '' || !parser.parsedData.has(colName)) {
            // reset colors
            renderer.resetPointColors()
            return
        }
        const colArr = parser.parsedData.get(colName) as Float32Array
        renderer.setXColumn(colArr)
    }

    const selectElY = document.getElementById('select-yaxis') as HTMLSelectElement
    selectElY.options.length = 0;
    selectElY.onchange = () => {
        const colName = selectElY.value
        if (colName === '' || !parser.parsedData.has(colName)) {
            // reset colors
            renderer.resetPointColors()
            return
        }
        const colArr = parser.parsedData.get(colName) as Float32Array
        renderer.setYColumn(colArr)
    }

    // Z axis
    const selectElZ = document.getElementById('select-zaxis') as HTMLSelectElement;
    selectElZ.options.length = 0;
    selectElZ.onchange = () => {
        const colName = selectElZ.value
        if (colName === '' || !parser.parsedData.has(colName)) {
            // reset colors
            renderer.resetPointColors()
            return
        }
        const colArr = parser.parsedData.get(colName) as Float32Array
        renderer.setZColumn(colArr)
    }

    //Dimension switch
    const dimensionSelect = document.getElementById('dimension-switch') as HTMLSelectElement;
    dimensionSelect.onchange = () => {
        const newDim = dimensionSelect.value
        const zCon = document.getElementById('z-axis-container') as HTMLDivElement
        if (newDim === '3D') {
            zCon.classList.remove('hidden')
        }
        else if (newDim === '2D') {
            zCon.classList.add('hidden')
        }
        else return;
        renderer.changeDimension(newDim)
    }

    // A blank option lets the coloring be switched off again
    const noColor = document.createElement('option')
    noColor.value = ''
    noColor.textContent = 'None'
    colorSelect.appendChild(noColor)
    cols.forEach((colName: string) => {
        createSelOption(colorSelect, colName)
        createSelOption(selectElX, colName)
        createSelOption(selectElY, colName)
        createSelOption(selectElZ, colName)
    })
    for (const sel of [colorSelect, selectElX, selectElY, selectElZ]) sel.disabled = false

    renderer.renderColumns()
    renderer.resetPointColors()

}

function selectEl(id: string): HTMLSelectElement {
    return document.getElementById(id) as HTMLSelectElement
}

// Choose an option and run its change handler. Values the select doesn't have are skipped.
function chooseOption(select: HTMLSelectElement, value: string) {
    if (!Array.from(select.options).some((o) => o.value === value)) return;
    select.value = value
    select.dispatchEvent(new Event('change'))
}

export function getViewState(): ViewState {
    const filterList = document.getElementById('filter-list') as HTMLDivElement;
    return {
        dimension: selectEl('dimension-switch').value,
        x: selectEl('select-xaxis').value,
        y: selectEl('select-yaxis').value,
        z: selectEl('select-zaxis').value,
        color: selectEl('color-select').value,
        filters: Array.from(filterList.children, readFilterRow),
        hiddenCategories: Array.from(legendHidden),
    }
}

export function applyViewState(state: ViewState) {
    // Filters first, so the axes are framed around the points they leave
    const filterList = document.getElementById('filter-list') as HTMLDivElement;
    filterList.replaceChildren()
    for (const action of input.getActions()) {
        if (action.group === 'Filters') input.unregisterAction(action.id)
    }
    state.filters.forEach((filter) => addFilterRow(filter))
    applyFilters()

    chooseOption(selectEl('dimension-switch'), state.dimension)
    chooseOption(selectEl('select-xaxis'), state.x)
    chooseOption(selectEl('select-yaxis'), state.y)
    chooseOption(selectEl('select-zaxis'), state.z)
    chooseOption(selectEl('color-select'), state.color)
    legendHidden = new Set(state.hiddenCategories)
    applyLegend()
    renderer.fitToVisible()
}

export { setupHeader, chooseOption }
