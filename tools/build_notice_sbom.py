"""Scan a built Docker image and embed its SBOM in a derived distribution image.

Requires existing docker and syft; never installs tools or pushes an image.
The caller must supply a scratch output directory outside the repository.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_image')
    parser.add_argument('output_image')
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    for tool in ('docker', 'syft'):
        if not shutil.which(tool):
            parser.error(f'{tool} is unavailable; provision it through the authorized environment before retrying')
    if args.source_image == args.output_image:
        parser.error('source and output image tags must differ')
    repo = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve()
    if output == repo or repo in output.parents:
        parser.error('output-dir must be a scratch directory outside the source repository')
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error('output-dir must be empty to avoid sending unrelated scratch files to Docker')
    image_id = run('docker', 'image', 'inspect', '--format', '{{.Id}}', args.source_image).strip()
    checker = (repo / 'tools/check_image_notices.py').read_text(encoding='utf-8')
    evidence = run('docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python', image_id, '-c', checker)
    json.loads(evidence)
    (output / 'installed-notices.json').write_text(evidence, encoding='utf-8')
    syft_version = run('syft', 'version').strip()
    # Explicit Docker source, no registry resolution. Syft catalogs OS and language packages.
    run('syft', 'scan', 'docker:' + image_id, '-o', 'spdx-json=' + str(output / 'image.spdx.json'))
    sbom = json.loads((output / 'image.spdx.json').read_text(encoding='utf-8'))
    if not sbom.get('packages'):
        raise RuntimeError('scanner returned an empty package inventory')
    locators = [ref.get('referenceLocator', '') for package in sbom['packages']
                for ref in package.get('externalRefs', [])]
    if not any(value.startswith('pkg:deb/') for value in locators):
        raise RuntimeError('Debian OS package inventory is missing from the SBOM')
    if not any(value.startswith('pkg:pypi/') for value in locators):
        raise RuntimeError('Python package inventory is missing from the SBOM')
    (output / 'scan-provenance.json').write_text(json.dumps({
        'source_image': args.source_image, 'source_image_id': image_id,
        'syft_version': syft_version,
        'scope': 'source image OS and language packages; derived image only adds these evidence files',
        'commercial_release_approval': False,
    }, indent=2) + '\n', encoding='utf-8')
    dockerfile = output / 'Dockerfile.notices'
    dockerfile.write_text('ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n'
        'COPY image.spdx.json installed-notices.json scan-provenance.json /app/sbom/\n', encoding='utf-8')
    run('docker', 'build', '--network', 'none', '--build-arg', 'BASE_IMAGE=' + image_id,
        '-f', str(dockerfile), '-t', args.output_image, str(output))
    run('docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python', args.output_image, '-c', checker)
    embedded = run('docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'cat', args.output_image, '/app/sbom/image.spdx.json')
    if json.loads(embedded) != sbom:
        raise RuntimeError('embedded SBOM differs from scanned evidence')
    print(json.dumps({'output_image': args.output_image, 'source_image_id': image_id, 'sbom': '/app/sbom/image.spdx.json'}))


if __name__ == '__main__':
    main()
