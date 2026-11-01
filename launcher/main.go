package main

import (
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

//go:embed all:payload
var application embed.FS

func prepare(directory string) error {
	if err := os.MkdirAll(directory, 0700); err != nil {
		return err
	}
	return fs.WalkDir(application, "payload", func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		name := strings.TrimPrefix(path, "payload/")
		if name == path || strings.ContainsAny(name, `/\`) || name == "." || name == ".." {
			return errors.New("invalid bundled file")
		}
		data, err := application.ReadFile(path)
		if err != nil {
			return err
		}
		target := filepath.Join(directory, name)
		if info, err := os.Lstat(target); err == nil && info.Mode()&os.ModeSymlink != 0 {
			return errors.New("bundled file path is a symbolic link")
		} else if err != nil && !os.IsNotExist(err) {
			return err
		}
		if err := os.WriteFile(target, data, 0644); err != nil {
			return err
		}
		return os.Chmod(target, 0644)
	})
}

func compose(directory string, args ...string) *exec.Cmd {
	project := os.Getenv("BETTERHOSTER_PROJECT_NAME")
	if project == "" {
		project = "betterhoster"
	}
	cmd := exec.Command("docker", append([]string{"compose", "--project-name", project}, args...)...)
	cmd.Dir = directory
	return cmd
}

func dashboardURL(directory string) (string, error) {
	cmd := compose(directory, "port", "dashboard", "8000")
	output, err := cmd.Output()
	if err != nil {
		return "", err
	}
	_, port, err := net.SplitHostPort(strings.TrimSpace(strings.Split(string(output), "\n")[0]))
	if err != nil {
		return "", err
	}
	return "http://127.0.0.1:" + port + "/", nil
}

func waitForDashboard(url string) error {
	client := &http.Client{Timeout: 2 * time.Second}
	deadline := time.Now().Add(90 * time.Second)
	for time.Now().Before(deadline) {
		response, err := client.Get(url)
		if err == nil {
			response.Body.Close()
			if response.StatusCode == http.StatusOK || response.StatusCode == http.StatusFound {
				return nil
			}
		}
		time.Sleep(time.Second)
	}
	return errors.New("the dashboard did not become ready")
}

func openBrowser(url string) {
	var cmd *exec.Cmd
	if runtime.GOOS == "windows" {
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", url)
	} else {
		cmd = exec.Command("xdg-open", url)
	}
	if err := cmd.Start(); err == nil {
		_ = cmd.Process.Release()
	}
}

func run() error {
	if len(os.Args) == 2 && os.Args[1] == "--version" {
		b, _ := json.Marshal(map[string]string{"name": "BetterHoster", "launcher": "1.0"})
		fmt.Println(string(b))
		return nil
	}
	config, err := os.UserConfigDir()
	if err != nil {
		return err
	}
	directory := filepath.Join(config, "BetterHoster")
	if err := prepare(directory); err != nil {
		return fmt.Errorf("unable to prepare BetterHoster: %w", err)
	}
	fmt.Println("Starting BetterHoster with Docker Compose...")
	command := compose(directory, "up", "-d", "--build", "--wait")
	command.Stdout, command.Stderr, command.Stdin = os.Stdout, os.Stderr, os.Stdin
	if err := command.Run(); err != nil {
		return fmt.Errorf("Docker Compose could not start BetterHoster: %w", err)
	}
	url, err := dashboardURL(directory)
	if err != nil {
		return fmt.Errorf("unable to find the dashboard port: %w", err)
	}
	if err := waitForDashboard(url); err != nil {
		return err
	}
	fmt.Println("BetterHoster is available at", url)
	if os.Getenv("BETTERHOSTER_NO_BROWSER") != "1" {
		openBrowser(url)
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "BetterHoster:", err)
		if runtime.GOOS == "windows" {
			fmt.Fprintln(os.Stderr, "Press Enter to close.")
			_, _ = fmt.Scanln()
		}
		os.Exit(1)
	}
}
