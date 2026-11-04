import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { newMessage } from './toast';
import * as d3 from 'd3';


const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0f1d1a)
const orthoCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1_000_000_000);
const perspCamera = new THREE.PerspectiveCamera();
let controls: OrbitControls;
export let camera: THREE.OrthographicCamera | THREE.PerspectiveCamera = orthoCamera;
camera.position.z = 2
//position must be > 0 so points are visible in frustum (cube)
const pointsGeometry = new THREE.BufferGeometry();
// sizeAttenuation off: size is in pixels in 3D too, instead of shrinking to nothing when the camera backs off
// Size is in device pixels: one screen pixel to start, on any display
export const pointsMaterial = new THREE.PointsMaterial({ size: window.devicePixelRatio, vertexColors: true, transparent: true, sizeAttenuation: false });
let pointsTransparency: number = 1; //between 0 and 1

const decimalPlaces: number = 2;

const mouseVector = new THREE.Vector2();
const raycaster = new THREE.Raycaster();
// Define a plane on the XY axis (Z = 0)
const xyPlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
// Compute world coordinates
const worldMousePoint = new THREE.Vector3();

const mouseLeftElement = document.getElementById('mouse-left') as HTMLSpanElement;
const mouseRightElement = document.getElementById('mouse-right') as HTMLSpanElement;

let lastMouseDownX: number;
let lastMouseDownY: number;

function floatEqual(x1: number, x2: number, epsilon: number = 0.000001): boolean {
    return Math.abs(x1 - x2) <= epsilon;
}

function animate(canvas: HTMLCanvasElement) {
    canvas.addEventListener('mousemove', onMouseMove);
    canvas.addEventListener('mousedown', (ev) => {
        lastMouseDownX = ev.clientX;
        lastMouseDownY = ev.clientY;
    })
    canvas.addEventListener('mouseup', (ev) => {
        const newX = ev.clientX;
        const newY = ev.clientY;
        if (floatEqual(lastMouseDownX, newX) && floatEqual(lastMouseDownY, newY)) {
            onMouseClick(ev)
        }
    })
    const renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
    // Draw at the screen's real resolution so points aren't blurry on Retina displays
    renderer.setPixelRatio(window.devicePixelRatio);
    // Fill the area below the header, and follow it when the window resizes
    const container = canvas.parentElement as HTMLElement;
    const resize = () => {
        const width = container.clientWidth;
        const height = container.clientHeight;
        if (width === 0 || height === 0) return;
        renderer.setSize(width, height);
        perspCamera.aspect = width / height;
        perspCamera.updateProjectionMatrix();
    };
    resize();
    new ResizeObserver(resize).observe(container);
    controls = new OrbitControls(camera, renderer.domElement);
    // Disable all 3D interaction
    controls.enableRotate = false;
    controls.enableZoom = true;
    controls.screenSpacePanning = true; // Pan along XY-plane

    // Optionally tweak speed
    controls.panSpeed = 1.0;

    // Map both left and right buttons to pan
    controls.mouseButtons.LEFT = THREE.MOUSE.PAN;
    controls.mouseButtons.RIGHT = THREE.MOUSE.PAN;

    // TODO: Show the x and y coordinates that are visible to the camera
    // TODO: make a toast module

    renderer.setAnimationLoop(() => {
        controls.update()
        renderer.render(scene, camera);
        frameListeners.forEach((listener) => listener())
    })
}

// Overlays (axes, tooltip) that follow the camera redraw from here
const frameListeners = new Set<() => void>();
export function onFrame(listener: () => void) {
    frameListeners.add(listener);
}

export interface ViewBounds { left: number; right: number; top: number; bottom: number }

// Visible world rectangle in 2D. Null in 3D, where there is no flat rectangle.
export function getViewBounds(): ViewBounds | null {
    if (!(camera instanceof THREE.OrthographicCamera)) return null;
    const cx = (camera.left + camera.right) / 2;
    const cy = (camera.top + camera.bottom) / 2;
    const halfW = (camera.right - camera.left) / (2 * camera.zoom);
    const halfH = (camera.top - camera.bottom) / (2 * camera.zoom);
    return {
        left: camera.position.x + cx - halfW,
        right: camera.position.x + cx + halfW,
        top: camera.position.y + cy + halfH,
        bottom: camera.position.y + cy - halfH,
    };
}

// Mouse position in normalized device coordinates (-1 to 1) relative to the canvas
function setMouseVector(event: MouseEvent) {
    const rect = (event.target as HTMLElement).getBoundingClientRect();
    mouseVector.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouseVector.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
}

function onMouseMove(event: MouseEvent) {
    setMouseVector(event);
    const pos = getXYFromMouse();
    mouseLeftElement.textContent = pos.x.toFixed(decimalPlaces)
    mouseRightElement.textContent = pos.y.toFixed(decimalPlaces)
}

function onMouseClick(event: MouseEvent) {
    setMouseVector(event);
    const pos = getXYFromMouse();
    newMessage(`Click @ (${pos.x.toFixed(decimalPlaces)}, ${pos.y.toFixed(decimalPlaces)})`)
}

function getXYFromMouse() {
    raycaster.setFromCamera(mouseVector, camera);
    raycaster.ray.intersectPlane(xyPlane, worldMousePoint);
    return worldMousePoint;
}

export function updateCameraBounds(x: number = 0, y: number = 0) {
    panCamera(x, y);
}

// Move the camera and the OrbitControls target together. If only the camera
// moves, controls.update() turns the camera back toward the old target and
// the 2D view tilts.
function panCamera(dx: number, dy: number) {
    camera.position.x += dx;
    camera.position.y += dy;
    if (controls) {
        controls.target.x += dx;
        controls.target.y += dy;
    }
}


const coordsPerPoint = 3;
const channelsPerColor = 4;
let pointPositionsBuffer: Float32Array;
let pointColorsBuffer: Float32Array;// x, y, z for each point
// 1 = shown, 0 = hidden. Kept separately so opacity changes don't undo filters.
let visibleMask: Uint8Array;
// Each feature that hides points (filters, the result switcher, the legend) owns
// one layer; a point is shown only if every layer lets it through.
const visibilityLayers = new Map<string, ArrayLike<number | boolean>>();

export function pointCount(): number {
    return pointColorsBuffer ? pointColorsBuffer.length / channelsPerColor : 0;
}

function alphaFor(i: number): number {
    return visibleMask[i] ? pointsTransparency : 0;
}

export function isVisible(i: number): boolean {
    return visibleMask[i] === 1;
}

function initDrawData(length: number) {
    pointPositionsBuffer = new Float32Array(length * coordsPerPoint);
    pointColorsBuffer = new Float32Array(length * channelsPerColor); // x, y, z for each point
    visibleMask = new Uint8Array(length).fill(1);
    visibilityLayers.clear();
    zValues = null;
}

// Pass null to remove the layer
export function setVisibilityLayer(name: string, mask: ArrayLike<number | boolean> | null) {
    if (!pointColorsBuffer) return;
    if (mask && mask.length !== pointCount())
        throw new Error(`Visibility mask should have one entry per point (${mask.length} != ${pointCount()})`)
    if (mask) visibilityLayers.set(name, mask);
    else visibilityLayers.delete(name);

    const layers = Array.from(visibilityLayers.values());
    for (let i = 0; i < visibleMask.length; i++) {
        let shown = 1;
        for (const layer of layers) {
            if (!layer[i]) { shown = 0; break; }
        }
        visibleMask[i] = shown;
        pointColorsBuffer[i * channelsPerColor + 3] = alphaFor(i);
    }
    // Not attached yet while a file is still loading; renderColumns uploads the buffer then
    const colors = pointsGeometry.getAttribute("color");
    if (colors) colors.needsUpdate = true;
}

// Extent of the shown points along one axis (0 = x, 1 = y, 2 = z). Falls back to all points when none are shown.
function visibleExtent(axis: number): [number, number] {
    let min = Infinity, max = -Infinity;
    for (const onlyVisible of [true, false]) {
        for (let i = 0; i < pointCount(); i++) {
            if (onlyVisible && !visibleMask[i]) continue;
            const v = pointPositionsBuffer[i * coordsPerPoint + axis];
            if (!Number.isFinite(v)) continue;
            if (v < min) min = v;
            if (v > max) max = v;
        }
        if (min <= max) break;
    }
    if (!(min <= max)) return [-1, 1];
    if (min === max) return [min - 1, max + 1];
    return [min, max];
}

// Fit the 2D view to the shown points along x and/or y, with a 5% margin
function frameOrtho(axes: { x?: boolean; y?: boolean }) {
    if (axes.x) {
        const [min, max] = visibleExtent(0);
        const pad = (max - min) * 0.05;
        orthoCamera.left = min - pad;
        orthoCamera.right = max + pad;
        orthoCamera.position.x = 0;
    }
    if (axes.y) {
        const [min, max] = visibleExtent(1);
        const pad = (max - min) * 0.05;
        orthoCamera.bottom = min - pad;
        orthoCamera.top = max + pad;
        orthoCamera.position.y = 0;
    }
    orthoCamera.updateProjectionMatrix();
    if (controls && camera === orthoCamera) controls.target.set(orthoCamera.position.x, orthoCamera.position.y, 0);
}

// Point the 3D camera at the middle of the shown points, far enough back to see all of them
function frame3D() {
    const [minX, maxX] = visibleExtent(0);
    const [minY, maxY] = visibleExtent(1);
    const [minZ, maxZ] = visibleExtent(2);
    const center = new THREE.Vector3((minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2);
    const radius = Math.max(1e-6, Math.hypot(maxX - minX, maxY - minY, maxZ - minZ) / 2);
    const vFov = (perspCamera.fov * Math.PI) / 180;
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * perspCamera.aspect);
    const dist = (radius / Math.sin(Math.min(vFov, hFov) / 2)) * 1.1;
    perspCamera.position.set(center.x, center.y, center.z + dist);
    perspCamera.near = dist / 1000;
    perspCamera.far = (dist + radius) * 10;
    perspCamera.zoom = 1;
    perspCamera.updateProjectionMatrix();
    if (controls && camera === perspCamera) controls.target.copy(center);
}

// Frame the shown points in whichever view is active
export function fitToVisible() {
    if (!pointPositionsBuffer) return;
    if (camera === orthoCamera) {
        orthoCamera.zoom = 1;
        frameOrtho({ x: true, y: true });
    } else frame3D();
}

const projected = new THREE.Vector3();

// Index of the shown point nearest the mouse, within maxPx screen pixels, or -1
export function nearestVisiblePoint(event: MouseEvent, maxPx: number): number {
    if (!pointPositionsBuffer) return -1;
    const rect = (event.target as HTMLElement).getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    let best = -1;
    let bestDist = maxPx * maxPx;
    for (let i = 0; i < pointCount(); i++) {
        if (!visibleMask[i]) continue;
        projected.fromArray(pointPositionsBuffer, i * coordsPerPoint).project(camera);
        if (projected.z < -1 || projected.z > 1) continue; // behind the camera or clipped
        const dx = (projected.x + 1) / 2 * rect.width - mx;
        const dy = (1 - projected.y) / 2 * rect.height - my;
        const d = dx * dx + dy * dy;
        if (d < bestDist) {
            bestDist = d;
            best = i;
        }
    }
    return best;
}

export function setXColumn(arr: ArrayLike<number>) {
    if (arr.length >= pointPositionsBuffer.length) throw new Error("Input array bigger than vertex buffer");
    for (let i = 0; i < arr.length; i++) {
        pointPositionsBuffer[i * coordsPerPoint] = arr[i];
    }
    pointsGeometry.getAttribute("position").needsUpdate = true;
    // Fit to the shown points, so a filtered or switched-to subset fills the view
    frameOrtho({ x: true });
    if (camera === perspCamera) frame3D();
}

export function setYColumn(arr: ArrayLike<number>) {
    if (arr.length >= pointPositionsBuffer.length) throw new Error("Input array bigger than vertex buffer");
    for (let i = 0; i < arr.length; i++) {
        pointPositionsBuffer[i * coordsPerPoint + 1] = arr[i];
    }
    pointsGeometry.getAttribute("position").needsUpdate = true;
    frameOrtho({ y: true });
    if (camera === perspCamera) frame3D();
}

// The chosen Z column. Only used in 3D: the 2D camera sits at z = 2 and doesn't
// draw anything behind it, so in 2D every point stays at z = 0.
let zValues: ArrayLike<number> | null = null;

function writeZ() {
    if (!pointPositionsBuffer) return;
    const use = camera === perspCamera ? zValues : null;
    for (let i = 0; i < pointCount(); i++) {
        pointPositionsBuffer[i * coordsPerPoint + 2] = use ? use[i] : 0;
    }
    const positions = pointsGeometry.getAttribute("position");
    if (positions) positions.needsUpdate = true;
}

export function setZColumn(arr: ArrayLike<number>) {
    if (arr.length >= pointPositionsBuffer.length) throw new Error("Input array bigger than vertex buffer");
    zValues = arr;
    writeZ();
    if (camera === perspCamera) frame3D();
}

// categoryOfRow[i] is point i's category index; the category gets palette[index]
export function colorMapRowsDiscrete(categoryOfRow: ArrayLike<number>, palette: readonly string[]) {
    usingDefaultColor = false;
    const colors = palette.map((css) => new THREE.Color(css));
    for (let i = 0; i < categoryOfRow.length; i++) {
        const color = colors[categoryOfRow[i] % colors.length]
        pointColorsBuffer[i * channelsPerColor] = color.r
        pointColorsBuffer[i * channelsPerColor + 1] = color.g
        pointColorsBuffer[i * channelsPerColor + 2] = color.b
    }
    pointsGeometry.getAttribute("color").needsUpdate = true;
}

export function colorMapRowsContinuous(rowData: ArrayLike<number>, colorScale: d3.ScaleSequential<any, any>) {
    usingDefaultColor = false;
    for (let i = 0; i < rowData.length; i++) {
        // Map the raw value from the domain to a color string
        const colorString = colorScale(rowData[i]);
        const color = d3.rgb(colorString);

        pointColorsBuffer[i * channelsPerColor] = color.r / 255; // R
        pointColorsBuffer[i * channelsPerColor + 1] = color.g / 255; // G
        pointColorsBuffer[i * channelsPerColor + 2] = color.b / 255; //B
    }

    // 5. Signal the GPU to upload the new data
    pointsGeometry.getAttribute("color").needsUpdate = true;
}

// Color of points when no color column is chosen: yellow on dark, near-black on light
let defaultPointColor: [number, number, number] = [1, 1, 0];
let usingDefaultColor = false;

export function resetPointColors() {
    setAllPointColors(...defaultPointColor);
    usingDefaultColor = true;
}

let backgroundMode: 'dark' | 'light' = 'dark';

export function getBackground(): 'dark' | 'light' {
    return backgroundMode;
}

export function setBackground(mode: 'dark' | 'light') {
    backgroundMode = mode;
    scene.background = new THREE.Color(mode === 'light' ? 0xffffff : 0x0f1d1a);
    defaultPointColor = mode === 'light' ? [0.08, 0.17, 0.15] : [1, 1, 0];
    if (usingDefaultColor && pointColorsBuffer) resetPointColors();
}

function setAllPointColors(red: number, green: number, blue: number) {
    usingDefaultColor = false;
    for (let i = 0; i < pointCount(); i++) {
        pointColorsBuffer[i * channelsPerColor] = red; // R
        pointColorsBuffer[i * channelsPerColor + 1] = green; // G
        pointColorsBuffer[i * channelsPerColor + 2] = blue; //B
        pointColorsBuffer[i * channelsPerColor + 3] = alphaFor(i); //opacity
    }
    pointsGeometry.getAttribute("color").needsUpdate = true;
}

function renderColumns() {
    pointsGeometry.setAttribute("position", new THREE.BufferAttribute(pointPositionsBuffer, coordsPerPoint))
    pointsGeometry.setAttribute("color", new THREE.BufferAttribute(pointColorsBuffer, channelsPerColor))
    drawPoints()
}

function clearPoints() {
    scene.clear()
}

function drawPoints() {
    const points = new THREE.Points(pointsGeometry, pointsMaterial)
    points.frustumCulled = false;
    points.geometry.computeBoundingSphere();
    scene.add(points)
}

export function changePointSize(size: number) {
    if (size <= 0) return;
    pointsMaterial.size = size;
}

export function changeTransparency(alpha: number) {
    if (alpha < 0 || alpha > 1) return;
    pointsTransparency = alpha;
    if (!pointColorsBuffer) return; // No file loaded yet
    for (let i = 0; i < pointCount(); i++) {
        pointColorsBuffer[i * channelsPerColor + 3] = alphaFor(i); //opacity
    }
    pointsGeometry.getAttribute("color").needsUpdate = true;
}

export function getPointsAlpha() {
    return pointsTransparency;
}

export function changeDimension(dim: string) {
    if (dim === '2D') {
        camera = orthoCamera;
        controls.object = orthoCamera;
        controls.enableRotate = false;
        // The 3D view moved the shared orbit target; put it back under the 2D camera
        controls.target.set(orthoCamera.position.x, orthoCamera.position.y, 0);
        writeZ(); // flatten
    } else if (dim === '3D') {
        camera = perspCamera;
        controls.object = perspCamera;
        controls.enableRotate = true;
        writeZ(); // lift the points to the Z column
        if (pointPositionsBuffer) frame3D();
    }
    controls.update();
}
// Move camera adjacent to xy plane
export function moveCameraLeft(delta: number) {
    panCamera(-delta, 0);
}
export function moveCameraRight(delta: number) {
    panCamera(delta, 0);
}
export function moveCameraUp(delta: number) {
    panCamera(0, delta);
}
export function moveCameraDown(delta: number) {
    panCamera(0, -delta);
}

export { animate, clearPoints, initDrawData, setAllPointColors, renderColumns }