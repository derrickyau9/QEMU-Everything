"""The first finite model grammar; interpreted natively by the research board."""
FIELDS = {'kind', 'write_address', 'read_address', 'mask', 'xor_value', 'value'}


def validate_model(model):
    if not isinstance(model, dict) or set(model) != FIELDS or model['kind'] not in ('constant', 'xor_latch'):
        raise ValueError('Unsupported model AST')
    for key in FIELDS - {'kind'}:
        if not isinstance(model[key], str):
            raise ValueError('Model integers must be explicit hex strings')
        value = int(model[key], 0)
        if not 0 <= value <= 0xffffffff:
            raise ValueError('Model integer outside uint32')
    if any(not 0x0b000000 <= int(model[k], 0) < 0x0b100000 or int(model[k], 0) % 4
           for k in ('write_address', 'read_address')):
        raise ValueError('Model rule outside the research MMIO aperture')
    return model
