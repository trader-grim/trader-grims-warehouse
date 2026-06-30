
def generate_dedupe_commands(sku_list: list[str], remote_root: str) -> list[str]:
    """Generates the dedupe commands for a list of SKUs."""
    commands = []
    for sku in sku_list:
        cmd = f"rclone dedupe --by-hash --dedupe-mode first {remote_root}/{sku} --log-file /opt/TGW/var/log/dedupe-{sku}.log"
        commands.append(cmd)
    return commands

def test_dedupe_command_generation():
    skus = ["tgw1", "tgw2"]
    remote = "dbukove:/TGW/data/ItemData"
    commands = generate_dedupe_commands(skus, remote)
    
    assert len(commands) == 2
    assert commands[0] == "rclone dedupe --by-hash --dedupe-mode first dbukove:/TGW/data/ItemData/tgw1 --log-file /opt/TGW/var/log/dedupe-tgw1.log"
    assert commands[1] == "rclone dedupe --by-hash --dedupe-mode first dbukove:/TGW/data/ItemData/tgw2 --log-file /opt/TGW/var/log/dedupe-tgw2.log"
