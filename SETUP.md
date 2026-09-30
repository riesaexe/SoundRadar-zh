# SoundRadar：完整 7.1 环绕声设置指南

完整的前后左右方向雷达需要游戏实际输出 7.1 声道，并由 SoundRadar 从虚拟 7.1 设备采集。SoundRadar 会把采集到的声音混成单声道后播放到耳机，因此 Windows 的“单声道音频”必须关闭。

## 首次设置

1. 安装并启动 VoiceMeeter Potato。安装程序需要管理员权限；安装后按提示重启 Windows。
2. 将 VoiceMeeter 的 `Voicemeeter VAIO3 Input` 配置为 7.1 声道：
   - 按 `Win+R`，输入 `mmsys.cpl` 并回车，打开“声音”控制面板。
   - 在“播放”选项卡选中 `Voicemeeter VAIO3 Input`，点“配置”。
   - 选择“7.1 环绕声”并完成配置。若只想让游戏声音进入 SoundRadar，请保留当前默认设备，并在下一步单独指定游戏的输出设备；只有希望其他应用也经过 VoiceMeeter 时，才将它设为默认设备。
3. 对每款游戏设置 Windows 输出设备：先启动游戏，再打开“设置 → 系统 → 声音 → 音量混合器”，把该游戏的输出设备选为 `Voicemeeter VAIO3 Input`。Windows 会按应用分别保存此设置。
4. 可选：右键游戏的 `.exe` →“属性”→“兼容性”→勾选“禁用全屏优化”，这可能有助于浮层显示。

VoiceMeeter Potato 的 VAIO3 虚拟输入支持最多 8 个声道；7.1 即 8 声道。若设备或选项没有出现，请确认 VoiceMeeter Potato 已正确安装并重启电脑。参考 [VB-Audio 官方产品说明](https://vb-audio.com/Voicemeeter/potato.htm)。

## 每次开机后的设置

1. 启动 VoiceMeeter。请将 VoiceMeeter 的 `A1` 输出留空；SoundRadar 会把混合后的声音直接播放到你选择的耳机。
2. 确认 Windows“单声道音频”已关闭：打开“设置 → 辅助功能 → 音频”，关闭“单声道音频”。Windows 10 中该选项位于“轻松使用 → 音频”。
3. 在游戏内把声道/扬声器模式设为 `7.1` 或 `8 声道`（如果游戏提供此选项）。
4. 打开 SoundRadar 的设置页：
   - “模式”选择“环绕声 — 7.1 声道，支持前后方向”。
   - “采集设备”选择 `Voicemeeter VAIO3 Input`。
   - “播放设备”选择实际使用的耳机。
   - 采集设备和播放设备必须不同，否则会形成音频反馈；SoundRadar 会拒绝此设置并退回立体声采集。
5. 退出并重新打开 SoundRadar，让采集模式和设备设置生效。可运行桌面快捷方式或 `SoundRadar.bat`；从源码启动则运行 `python run.py`。
6. 将游戏设为“无边框窗口”模式，让雷达浮层显示在游戏上方。

## 确认 7.1 是否生效

在 SoundRadar 设置页打开“检测”选项卡，再播放方向明显的游戏声音。完整 7.1 输入应显示 8 个声道音量条，且声音方向变化时各条的读数会不同。出现“已检测到方向”时，雷达即可使用前后左右方向。

- **只看到两个声道有声音**：游戏仍在输出立体声。把游戏内声道改为 7.1，并在 Windows 音量混合器中将该游戏的输出设备设为 `Voicemeeter VAIO3 Input`。
- **所有声道音量条变化一致**：关闭 Windows“单声道音频”，并确认游戏与采集设备都处于 7.1 模式。
- **音量条完全没有变化**：确认游戏正在播放声音，且 Windows 音量混合器中的游戏输出设备设为 `Voicemeeter VAIO3 Input`。
- **游戏声音听不见**：确认 SoundRadar 的“播放设备”选的是耳机，并且该设备与“采集设备”不同。

## 立体声模式

不需要安装或配置 VoiceMeeter。设置页将“模式”改为“立体声 — 无需设置，仅左右方向”即可。此模式只有左右方向，没有前后方向。也可从命令行运行 `python run.py --all-apps`；Windows“单声道音频”无需关闭。
