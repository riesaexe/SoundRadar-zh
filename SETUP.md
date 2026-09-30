# SoundRadar：完整 7.1 环绕声设置指南

前后左右方向需要游戏实际输出 7.1 声道，并让 SoundRadar 从一个提供 8 个独立声道的播放设备采集。可以直接使用原生 7.1 声卡/HDMI 接收设备；如果耳机端点只有立体声，推荐用 VB-CABLE 创建虚拟 7.1 端点。**不需要 VoiceMeeter Potato。**SoundRadar 会把采集到的声道混成单声道后播放到耳机，因此 Windows 的“单声道音频”和 Windows Sonic 必须关闭。

## 推荐方案：VB-CABLE

1. 从 [VB-Audio 官方页面](https://vb-audio.com/Cable/) 下载并安装标准 VB-CABLE 驱动。安装需要管理员权限，之后按提示重启 Windows。
2. 配置 VB-CABLE 的播放端点：
   - 按 `Win+R`，输入 `mmsys.cpl` 并回车，打开“声音”控制面板。
   - 在“播放”选项卡选中 `CABLE Input`（Windows 可能显示为“扬声器 (VB-Audio Virtual Cable)”），点“配置”。
   - 选择“7.1 环绕声”并完成配置。不要选录音端点 `CABLE Output`，也不要选另一个默认只有 2 声道的 `CABLE In 16ch` 端点。
   - 安装器可能会把 VB-CABLE 设为默认播放设备。如果不想改变全系统音频输出，请把原设备设回默认，之后只为游戏单独指定 VB-CABLE。
3. 启动游戏，打开 Windows“设置 → 系统 → 声音 → 音量混合器”，将该游戏的输出设备设为 `CABLE Input`。同时在游戏内把扬声器/声道模式设为 `7.1` 或 `8 声道`。
4. 打开 SoundRadar 设置页：
   - “模式”选择“环绕声 — 7.1 声道，支持前后方向”。
   - “采集设备”选择 `CABLE Input (VB-Audio Virtual Cable)`。
   - “播放设备”选择实际使用的耳机。
   - 采集设备和播放设备必须不同，否则会形成反馈；SoundRadar 会拒绝该设置并退回立体声采集。
5. 确认 Windows“单声道音频”已关闭：打开“设置 → 辅助功能 → 音频”，关闭“单声道音频”。Windows 10 中该选项位于“轻松使用 → 音频”。关闭 Windows Sonic 和会把输出转换成双声道的空间音效。
6. 退出并重新打开 SoundRadar，让设备和采集模式设置生效。将游戏设为“无边框窗口”模式，让雷达浮层显示在游戏上方。

VB-CABLE 的 `CABLE Input` 是播放端点，`CABLE Output` 是对应的录音端点。SoundRadar 使用 WASAPI 回环采集，因此应选择 `CABLE Input`。此项目已在 Windows 10 上验证该端点能被 SoundRadar 枚举为 8 声道，并通过逐声道测试音确认声道分离。

## 原生 7.1 设备

如果声卡、HDMI 接收器或其他播放设备本身支持 7.1，可直接在 `mmsys.cpl` 中把它配置为“7.1 环绕声”，无需安装虚拟音频驱动。SoundRadar 的“采集设备”选择该播放端点，“播放设备”仍选择耳机。运行 `python run.py --list` 可检查设备是否报告 8 声道。

## 已有 VoiceMeeter Potato

VoiceMeeter 仍可作为另一种虚拟端点使用：将 `Voicemeeter VAIO3 Input` 配置为 7.1，把游戏输出设为该设备，然后在 SoundRadar 中选择它作为采集设备、耳机作为播放设备。无需让 VoiceMeeter 的 `A1` 输出到耳机，因为 SoundRadar 会播放混音结果。参考 [VB-Audio 官方产品说明](https://vb-audio.com/Voicemeeter/potato.htm)。

## 确认 7.1 是否生效

在 SoundRadar 设置页打开“检测”选项卡，再播放方向明显的游戏声音。完整 7.1 输入应显示 8 个声道音量条，且声音方向变化时各条的读数会不同。出现“已检测到方向”时，雷达即可使用前后左右方向。

- **只看到两个声道有声音**：在 `mmsys.cpl` 中确认 `CABLE Input` 已配置为 7.1，并确认游戏内声道模式和 Windows 音量混合器的输出设备设置正确；检查 SoundRadar 选择的是播放端点而不是 `CABLE Output`。
- **所有声道音量条变化一致**：关闭 Windows“单声道音频”及 Windows Sonic，确认游戏实际输出 7.1，而不是把立体声扩展到 8 个声道。
- **音量条完全没有变化**：确认游戏正在播放声音，且 Windows 音量混合器中的游戏输出设备与 SoundRadar 的采集设备相同。
- **游戏声音听不见**：确认 SoundRadar 的“播放设备”选的是耳机，并且该设备与“采集设备”不同。

## 立体声模式

不需要安装或配置任何虚拟音频设备。设置页将“模式”改为“立体声 — 无需设置，仅左右方向”即可。此模式只有左右方向，没有前后方向。也可从命令行运行 `python run.py --all-apps`；Windows“单声道音频”无需关闭。
