import re, sys

variant = sys.argv[1]  # e.g. veh_t1lfl1
base = f'cpj_chery/{variant}'
pdmdb_path = f'{base}/pdm_cfg/src/pdmdb.cpp'
handler_path = f'{base}/dia_cfg/src/0x31/ov_dia_0x31_routine_by_identifier_handler.cpp'

pdmdb = open(pdmdb_path, 'r', encoding='latin-1').read()

ram = None
for cand in ('Pdm_InternalCodingNew_Ram', 'Pdm_InternalCoding_Ram'):
    if f'unsigned char {cand}[200]' in pdmdb:
        ram = cand
        break
assert ram, f'no ram found in {pdmdb_path}'

blockbase = 'NVM_PDM_InternalCodingNew' if 'New' in ram else 'NVM_PDM_InternalCoding'
desc = f'NvMConf_NvMBlockDescriptor_{blockbase}'
length = f'NVM_CFG_NV_BLOCK_LENGTH_{blockbase}'

m = re.search(r'unsigned char\s+' + re.escape(ram) + r'\s*\[\s*200\s*\]\s*=\s*\{(.*?)\};', pdmdb, re.S)
assert m, 'default initializer not found'
body = m.group(1)
nums = re.findall(r'0x([0-9a-fA-F]{2})u?', body)
bytes_list = [int(x, 16) for x in nums]
assert len(bytes_list) == 200, f'got {len(bytes_list)} bytes'

val = bytes_list[1] << 8 | bytes_list[0]

lines = []
for i in range(0, 200, 16):
    chunk = bytes_list[i:i + 16]
    s = ','.join(f'0x{b:02x}u' for b in chunk)
    if i + 16 < 200:
        s += ','
    lines.append('        ' + s)
default_block = '\n'.join(lines)

new_block = (
    f"    /// @brief write internal config data (reset to factory default 0x{val:04X})\n"
    f"    zos::uint8_t l_internalCodingDefault_au8[{length}] =\n"
    "    {\n"
    + default_block + "\n"
    "    };\n"
    "\n"
    "    idia::idiaIdBasedReadWrite(\n"
    f"        {desc},\n"
    "        idia::E_DIA_WRITE_REQUEST,\n"
    "        l_internalCodingDefault_au8,\n"
    f"        {length});\n"
)

handler = open(handler_path, 'r', encoding='latin-1').read()

handler, n_ext = re.subn(
    r'extern unsigned char ' + re.escape(ram) + r'\[200\];\n', '', handler, count=1)
assert n_ext == 1, f'extern removal count = {n_ext}'

old_block = (
    r'    /// @brief write internal config data\n'
    r'    idia::idiaIdBasedReadWrite\(\n'
    r'        ' + re.escape(desc) + r',\n'
    r'        idia::E_DIA_WRITE_REQUEST,\n'
    r'        ' + re.escape(ram) + r',\n'
    r'        ' + re.escape(length) + r'\);\n'
)
handler, n_rep = re.subn(old_block, new_block, handler, count=1)
assert n_rep == 1, f'write block replace count = {n_rep}'

open(handler_path, 'w', encoding='latin-1', newline='').write(handler)
print(f'OK {variant}: ram={ram} block={blockbase} val=0x{val:04X}')
