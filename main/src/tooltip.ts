import * as renderer from './render';
import * as parser from './file';

// Hover a point to see its row from the CSV

const maxRows = 12; // wide files (64 pixel columns…) would fill the screen

export function initTooltip(canvas: HTMLCanvasElement) {
    const tip = document.getElementById('tooltip') as HTMLDivElement;
    let pending: MouseEvent | null = null;

    const hide = () => tip.classList.add('hidden');

    const update = (event: MouseEvent) => {
        if (parser.rows.length < 2) return hide();
        // Points are drawn as squares of this many pixels; allow a little slack around them
        const radius = Math.max(6, renderer.pointsMaterial.size / window.devicePixelRatio / 2 + 4);
        const index = renderer.nearestVisiblePoint(event, radius);
        if (index < 0) return hide();

        const row = parser.rows[index + 1];
        const value = (id: string) => (document.getElementById(id) as HTMLSelectElement).value;
        // The plotted columns first, then the rest in file order
        const is3D = value('dimension-switch') === '3D';
        const first = [value('select-xaxis'), value('select-yaxis'), is3D ? value('select-zaxis') : '', value('color-select')];
        const order = Array.from(new Set([...first.filter((c) => c && parser.columns.includes(c)), ...parser.columns]));

        const table = document.createElement('dl');
        for (const col of order.slice(0, maxRows)) {
            const dt = document.createElement('dt');
            dt.textContent = col;
            const dd = document.createElement('dd');
            dd.textContent = row[parser.getColIndex(col)] ?? '';
            table.append(dt, dd);
        }
        const head = document.createElement('div');
        head.className = 'tooltip-head';
        head.textContent = `Row ${index + 1}`;
        tip.replaceChildren(head, table);
        if (order.length > maxRows) {
            const more = document.createElement('div');
            more.className = 'tooltip-more';
            more.textContent = `+${order.length - maxRows} more columns`;
            tip.appendChild(more);
        }

        tip.classList.remove('hidden');
        // Beside the cursor, flipped to the other side near the right or bottom edge
        const stage = canvas.getBoundingClientRect();
        let left = event.clientX - stage.left + 14;
        let top = event.clientY - stage.top + 14;
        if (left + tip.offsetWidth > stage.width - 8) left -= tip.offsetWidth + 28;
        if (top + tip.offsetHeight > stage.height - 8) top -= tip.offsetHeight + 28;
        tip.style.left = `${Math.max(4, left)}px`;
        tip.style.top = `${Math.max(4, top)}px`;
    };

    canvas.addEventListener('mousemove', (event) => {
        if (event.buttons !== 0) { // panning or orbiting
            hide();
            return;
        }
        // At most one lookup per frame
        if (!pending) requestAnimationFrame(() => {
            if (pending) update(pending);
            pending = null;
        });
        pending = event;
    });
    canvas.addEventListener('mouseleave', () => {
        pending = null;
        hide();
    });
}
