import * as toast from './toast';

// Datasets served from public/samples, so a visitor can try the app without a file of their own
interface Sample {
    file: string;  // under public/samples; also the file name favorites are saved under
    label: string;
    size: string;
    originalFile?: string;
    description?: string;
    citation?: string;
}


const samples: Sample[] = [
    {
        file: 't47d.csv',
        label: 'T47D cell cycle',
        size: '18 MB',
        description: 'A dataset containing measurements of T47D breast cancer cells, including cell-cycle information and biological markers. It can be used to explore patterns in cell-cycle behavior.',
        citation: 'Source: Tarek Zikry and Jeremy Purvis (citation pending confirmation).'
    },
    {
        file: 'mnist_phate.csv',
        label: 'MNIST — PHATE',
        size: 'Processed dataset',
        originalFile: 'mnist_original.zip',
        description: 'A dataset of 60,000 handwritten digit images (0–9), represented using PHATE dimensionality reduction. It can be used to explore how handwritten digits cluster in a lower-dimensional space.',
        citation: 'LeCun, Y., Bottou, L., Bengio, Y., & Haffner, P. (1998). Gradient-based learning applied to document recognition. Proceedings of the IEEE, 86(11), 2278–2324. DOI: 10.1109/5.726791'
    },
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
        
        const row = document.createElement('div');
        row.className = 'sample-row';
        row.style.display = 'flex';
        row.style.alignItems = 'center';
        row.style.gap = '8px';
        row.style.position = 'relative';

        btn.style.flex = '1';
        btn.style.minWidth = '0';

        row.appendChild(btn);

        // Information button and popup
        if (sample.description || sample.citation) {
            const info = document.createElement('button');
            info.type = 'button';
            info.textContent = 'ⓘ';
            info.title = `Information about ${sample.label}`;
            info.setAttribute('aria-label', info.title);
            info.setAttribute('aria-expanded', 'false');

            info.style.padding = '8px';
            info.style.fontSize = '18px';
            info.style.cursor = 'pointer';
            info.style.flexShrink = '0';
            info.style.background = 'transparent';
            info.style.border = 'none';

            const popup = document.createElement('div');
            popup.style.display = 'none';
            popup.style.position = 'absolute';
            popup.style.top = '100%';
            popup.style.right = '0';
            popup.style.width = '280px';
            popup.style.maxWidth = '90vw';
            popup.style.padding = '12px';
            popup.style.background = '#fff';
            popup.style.color = '#222';
            popup.style.border = '1px solid #ddd';
            popup.style.borderRadius = '8px';
            popup.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
            popup.style.zIndex = '1000';
            popup.style.fontSize = '13px';
            popup.style.lineHeight = '1.5';

            const heading = document.createElement('strong');
            heading.textContent = sample.label;

            const description = document.createElement('p');
            description.textContent = sample.description ?? '';

            const citation = document.createElement('p');
            citation.textContent = sample.citation ?? '';
            citation.style.fontSize = '11px';
            citation.style.marginBottom = '0';

            popup.append(heading, description, citation);

            info.onclick = (event) => {
                event.stopPropagation();
                const isOpen = popup.style.display !== 'none';
                popup.style.display = isOpen ? 'none' : 'block';
                info.setAttribute('aria-expanded', String(!isOpen));
            };

            row.append(info, popup);
        }

        // Original dataset download button
        if (sample.originalFile) {
            const download = document.createElement('a');
            download.href = `${import.meta.env.BASE_URL}samples/${sample.originalFile}`;
            download.download = sample.originalFile;
            download.textContent = '↓';
            download.title = `Download original dataset for ${sample.label}`;
            download.setAttribute('aria-label', download.title);

            download.style.padding = '8px';
            download.style.fontSize = '20px';
            download.style.textDecoration = 'none';
            download.style.flexShrink = '0';

            download.onclick = (event) => {
                event.stopPropagation();
                dropdown.classList.add('hidden');
            };

            row.appendChild(download);
        }

        list.appendChild(row);

        return btn;
    });
}
