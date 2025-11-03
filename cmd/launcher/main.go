package main

import (
    "fmt"
    "os"
    "os/exec"
    "path/filepath"
)

func main() {
    exePath, err := os.Executable()
    if err != nil {
        fmt.Fprintf(os.Stderr, "Не удалось определить путь к exe: %v\n", err)
        return
    }
    baseDir := filepath.Dir(exePath)
    scriptPath := filepath.Join(baseDir, "bot_app", "main.py")
    if _, err := os.Stat(scriptPath); err != nil {
        fmt.Fprintf(os.Stderr, "Не найден скрипт бота: %s\n", scriptPath)
        return
    }

    candidates := []string{
        filepath.Join(baseDir, "python", "pythonw.exe"),
        filepath.Join(baseDir, "python", "python.exe"),
        "pythonw",
        "python",
    }

    var pythonExe string
    for _, candidate := range candidates {
        if path, err := exec.LookPath(candidate); err == nil {
            pythonExe = path
            break
        }
    }

    if pythonExe == "" {
        message := "Не удалось найти интерпретатор Python. Установите Python 3.11+ или добавьте python.exe рядом с программой."
        fmt.Fprintln(os.Stderr, message)
        fmt.Fprintln(os.Stdout, message)
        return
    }

    cmd := exec.Command(pythonExe, scriptPath)
    cmd.Stdout = os.Stdout
    cmd.Stderr = os.Stderr
    cmd.Env = append(os.Environ(), "PYTHONUTF8=1")

    if err := cmd.Run(); err != nil {
        fmt.Fprintf(os.Stderr, "Ошибка запуска python скрипта: %v\n", err)
    }
}
