import * as toast from './toast';

// Datasets served from public/samples, so a visitor can try the app without a file of their own
interface Sample {
    file: string;  // under public/samples; also the file name favorites are saved under
    label: string;
    size: string;
}

const samples: Sample[] = [
    { file: 't47d.csv', label: 'T47D cell cycle', size: '18 MB' },
];

// Called once at startup with the same loader Open CSV uses
export function initSamples(open: (file: File) => Promise<void>) {
    const list = document.getElementById('samples-list') as HTMLDivElement;
    const dropdown = list.closest('.dropdown') as HTMLDivElement;
    const buttons = samples.map((sample) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'sample-item';
        const name = document.createElement('span');
        name.textContent = sample.label;
        const detail = document.createElement('small');
        detail.textContent = `${sample.file} · ${sample.size}`;
        btn.append(name, detail);
        btn.onclick = async () => {
            dropdown.classList.add('hidden');
            buttons.forEach((b) => b.disabled = true);
            toast.newMessage(`Downloading ${sample.label} (${sample.size})...`, 60_000);
            try {
                const response = await fetch(`${import.meta.env.BASE_URL}samples/${sample.file}`);
                if (!response.ok) throw new Error(`Couldn't download ${sample.file} (HTTP ${response.status})`);
                const blob = await response.blob();
                await open(new File([blob], sample.file, { type: 'text/csv' }));
            } finally {
                buttons.forEach((b) => b.disabled = false);
            }
        };
        list.appendChild(btn);
        return btn;
    });
}
