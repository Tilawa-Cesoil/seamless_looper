把便携版 ffmpeg 放在这里
========================

如需制作“拷贝即用”的 Windows 版本，可将以下两个文件放到本目录：

    bin\ffmpeg.exe
    bin\ffprobe.exe

程序会优先使用环境变量指定的路径；否则自动查找 bin\、ffmpeg\bin\ 或
vendor\ffmpeg\bin\ 下的可执行文件；最后才查找系统 PATH。

分发前请遵守 ffmpeg 的许可证（GPL / LGPL，取决于构建选项），并在项目的
关于页面或 README 中保留相应声明。若使用 GPL 构建，整个分发物可能需要
遵循 GPL 的兼容条款；商业闭源分发建议仔细评估或使用 LGPL 构建。
