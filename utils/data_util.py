def extract_plex_json(s):
    # 检查输入是否是字节串，如果不是，则将其转换为字节串
    if isinstance(s, str):
        s = s.encode('utf-8')  # 假设字符串是 UTF-8 编码的

    # 查找起始位置
    start_index = s.find(b'\r\n{')  # 在字节串上使用字节串进行查找
    if start_index == -1:
        return None  # 如果找不到起始位置，则返回 None

    # 查找结束位置
    end_index = s.find(b'}\r\n', start_index)  # 在字节串上使用字节串进行查找
    if end_index == -1:
        return None  # 如果找不到结束位置，则返回 None

    # 截取 JSON 字符串
    json_bytes = s[start_index + 2:end_index + 3]  # 加上起始位置偏移量和长度

    # 将字节串解码为字符串
    json_str = json_bytes.decode('utf-8')  # 假设 JSON 字符串是 UTF-8 编码的

    return json_str
