param([string]$xlsxPath, [string]$outPath)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.ScreenUpdating = $false

    $wb = $excel.Workbooks.Open($xlsxPath)
    $ws = $wb.Sheets.Item(1)

    $range = $ws.Range('A1:U21')
    $range.CopyPicture(1, 2)

    # Small delay to let clipboard settle
    Start-Sleep -Milliseconds 500

    if ([System.Windows.Forms.Clipboard]::ContainsImage()) {
        $img = [System.Windows.Forms.Clipboard]::GetImage()
        $img.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
        $img.Dispose()
        Write-Output "OK"
    } else {
        Write-Output "ERROR: No image on clipboard"
    }

    $wb.Close($false)
    $excel.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excel) | Out-Null
    [System.GC]::Collect()
} catch {
    Write-Output "ERROR: $_"
    try { $wb.Close($false) } catch {}
    try { $excel.Quit() } catch {}
}
