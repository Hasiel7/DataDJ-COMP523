export type MidiStatus = 'pending' | 'unsupported' | 'denied' | 'no-device' | 'connected';
type StatusCallback = (status: MidiStatus, deviceName?: string) => void;
type MidiListener = (data: Uint8Array, portName: string) => void;

// Listeners can be registered before access is granted. Every input port is
// watched, so a device plugged in later still works.
const listeners: MidiListener[] = [];
const watchedInputs = new Set<MIDIInput>();
let statusCallback: StatusCallback = () => { };

export function onStatusChange(callback: StatusCallback) {
    statusCallback = callback;
}

export async function init() {
    // Safari and Firefox do not implement Web MIDI.
    if (!('requestMIDIAccess' in navigator)) {
        statusCallback('unsupported');
        return;
    }
    statusCallback('pending');
    try {
        const access = await navigator.requestMIDIAccess();
        watchInputs(access);
        access.onstatechange = (event) => {
            const port = (event as MIDIConnectionEvent).port;
            console.log(`${port?.type}: Port ${port?.name} is now ${port?.state}`);
            watchInputs(access);
        };
    } catch (err) {
        console.error(`Failed to get MIDI access - ${err}`);
        statusCallback('denied');
    }
}

// The VMX8 has two input ports ("Master" and "Private") and sends different
// controls on each, so we listen to all of them. Messages carry the port name
// so the same CC number on two ports stays two different controls.
function watchInputs(access: MIDIAccess) {
    for (const input of access.inputs.values()) {
        if (watchedInputs.has(input)) continue;
        watchedInputs.add(input);
        input.addEventListener('midimessage', (event) => {
            const data = (event as MIDIMessageEvent).data;
            if (data) listeners.forEach((listener) => listener(data, input.name ?? 'MIDI port'));
        });
    }

    const connected = Array.from(access.inputs.values()).filter((input) => input.state === 'connected');
    if (connected.length === 0) {
        statusCallback('no-device');
        return;
    }
    // "SINCO VMX8-Master" and "SINCO VMX8-Private" -> "SINCO VMX8"
    const name = connected[0].name?.replace(/[-\s]+(Master|Private)$/i, '') ?? 'MIDI device';
    statusCallback('connected', name);
}

export function addMidiEventListener(callback: MidiListener) {
    listeners.push(callback);
}
