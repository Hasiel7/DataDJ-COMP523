import * as d3 from 'd3';
import * as renderer from './render';
import * as parser from './file';

// Tick marks, numbers and column names along the bottom and left edges of the 2D view.
// Hidden in 3D, where there is no flat edge to put them on.

export function initAxes() {
    const svg = document.getElementById('axes') as unknown as SVGSVGElement;
    const xSelect = document.getElementById('select-xaxis') as HTMLSelectElement;
    const ySelect = document.getElementById('select-yaxis') as HTMLSelectElement;
    let lastKey = '';

    renderer.onFrame(() => {
        const bounds = renderer.getViewBounds();
        const width = svg.clientWidth;
        const height = svg.clientHeight;
        const ready = bounds && parser.rows.length > 1 && xSelect.value && ySelect.value;
        // Redraw only when something moved
        const key = ready ? [bounds.left, bounds.right, bounds.top, bounds.bottom, width, height, xSelect.value, ySelect.value].join() : '';
        if (key === lastKey) return;
        lastKey = key;
        if (!ready) {
            svg.replaceChildren();
            return;
        }
        draw(svg, bounds, width, height, xSelect.value, ySelect.value);
    });
}

function draw(svgEl: SVGSVGElement, bounds: renderer.ViewBounds, width: number, height: number, xName: string, yName: string) {
    const x = d3.scaleLinear([bounds.left, bounds.right], [0, width]);
    const y = d3.scaleLinear([bounds.bottom, bounds.top], [height, 0]);
    const xCount = Math.max(2, Math.floor(width / 100));
    const yCount = Math.max(2, Math.floor(height / 70));
    const xFormat = x.tickFormat(xCount);
    const yFormat = y.tickFormat(yCount);
    // Leave room at the corners so labels don't run into each other or off the edge
    const xTicks = x.ticks(xCount).filter((t) => x(t) > 40 && x(t) < width - 30);
    const yTicks = y.ticks(yCount).filter((t) => y(t) > 30 && y(t) < height - 30);

    const svg = d3.select(svgEl);
    svg.selectAll('*').remove();

    const xAxis = svg.append('g').attr('class', 'axes-ticks');
    xAxis.selectAll('line').data(xTicks).join('line')
        .attr('x1', (t) => x(t)).attr('x2', (t) => x(t)).attr('y1', height).attr('y2', height - 6);
    xAxis.selectAll('text').data(xTicks).join('text')
        .attr('x', (t) => x(t)).attr('y', height - 10).attr('text-anchor', 'middle').text((t) => xFormat(t));

    const yAxis = svg.append('g').attr('class', 'axes-ticks');
    yAxis.selectAll('line').data(yTicks).join('line')
        .attr('y1', (t) => y(t)).attr('y2', (t) => y(t)).attr('x1', 0).attr('x2', 6);
    yAxis.selectAll('text').data(yTicks).join('text')
        .attr('x', 10).attr('y', (t) => y(t)).attr('dominant-baseline', 'middle').text((t) => yFormat(t));

    svg.append('text').attr('class', 'axes-title')
        .attr('x', width - 12).attr('y', height - 28).attr('text-anchor', 'end').text(`${xName} →`);
    svg.append('text').attr('class', 'axes-title')
        .attr('x', 12).attr('y', 20).text(`↑ ${yName}`);
}
