"""Camera research catalogue, with extraction and boot evidence kept separate."""
from .core import ASSETS, read_json


def camera_catalog():
    return read_json(ASSETS / 'camera_catalog.json')


def catalogue_markdown(language='en'):
    data = camera_catalog()
    zh = language == 'zh'
    lines = ['# Camera firmware research / 相机固件研究', '',
             'Updated / 更新: ' + data['updated'], '',
             '解包、内核启动、用户空间和原厂 UI 分别验证。' if zh else
             'Extraction, kernel boot, userspace and original UI are verified separately.', '']
    for row in data['families']:
        lines += ['## ' + row['brand'] + ' — ' + row['family'], '',
                  row['status_zh'] if zh else row['status_en'], '',
                  'Official firmware / 官方固件: ' + row['official_url'], '',
                  'Local sample verification / 本地样本验证: ' + ', '.join(row['verified_samples']), '',
                  'Boot gap / 启动缺口: ' + (row['boot_gap_zh'] if zh else row['boot_gap_en']), '']
        for project in row['upstreams']:
            lines += ['- ' + project['url'],
                      '  Revision / 版本: ' + project['revision'] + '; license / 许可: ' + project['license']]
        lines.append('')
    return '\n'.join(lines)
