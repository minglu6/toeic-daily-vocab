# 托业通勤听力（原创）

本目录是原创托业/商务英语通勤听力，**不是**新东方或其他商业词书的原文或录音。

每词朗读顺序：

1. 英文读音（en-US-JennyNeural，语速 -12%）
2. 字母拼写（连字符读作 hyphen，如 all-hands → A, L, L, hyphen, H, A, N, D, S）
3. 中文释义（zh-CN-XiaoxiaoNeural，语速 -8%）
4. 英文托业/商务场景例句
5. 例句汉语翻译

## 重新生成

```bash
# 从 days/*.html 及可选扩充词表重建词库
python3 scripts/build_commute_lexicon.py --extra commute/expansion-batch2.json

# 合成并拼接曲目（每轨约 30 词）
python3 scripts/generate_commute_audio.py
```

输出：

- `lexicon.json` 全词文案
- `scripts/001.json` 起为每轨 cue sheet
- `tracks/001.mp3` 起为可播放曲目
- `tracks.json` 播放页清单
- `feed.xml` RSS

分段缓存位于 `cache/`（不入库）。比特率 32k mono / 24 kHz。
