<#
.SYNOPSIS
    Bat / tat / kiem tra Kimodo GPU worker (ECS + ASG) — khong can nho lenh AWS.

.DESCRIPTION
    Kimodo chay theo 2 tang rieng biet, khong co ECS Service tu keo task
    (xem infra/infra/kimodo_ecs_stack.py:238-242):
      1. ASG `kimodo-asg` desired=1  -> tao may EC2 g5.xlarge (3-5 phut)
      2. `ecs run-task kimodo-mcp`   -> chay container worker.py tren may do

    Scale ASG ma quen run-task = ton $1.006/h (~$24/ngay) cho may trong.
    Tat thi lam nguoc lai: stop task truoc, scale ASG ve 0 sau.

    Moi tham so mang (security group, subnet) deu doc live tu CloudFormation
    va ASG — khong hardcode, doi stack van chay.

.EXAMPLE
    .\scripts\kimodo-task.ps1 status   # xem may + task dang chay hay khong
    .\scripts\kimodo-task.ps1 on       # bat: scale ASG -> doi InService -> run task
    .\scripts\kimodo-task.ps1 logs     # duoi log /ecs/kimodo (doi "model loaded")
    .\scripts\kimodo-task.ps1 off      # tat: stop task -> scale ASG ve 0

.NOTES
    - EC2 launch type KHONG dung assignPublicIp (se 400 InvalidParameterException).
    - Cold start ~5 phut tu run-task: pull image 3.26GB (~2p) + sync S3 17.8GB (~2-3p).
      San sang khi log co "model loaded, recovered ... abandoned job(s)".
    - Queue DynamoDB `vva-motion-jobs`: chi co row worker#heartbeat = worker song,
      chua co job nao. Enqueue that de test render 7s/job.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('on', 'off', 'status', 'logs')]
    [string]$Action = 'status',

    [string]$AsgName = 'kimodo-asg',
    [string]$Cluster = 'kimodo-cluster',
    [string]$TaskFamily = 'kimodo-mcp',
    [string]$StackName = 'VvaKimodoEcsStack',
    [string]$LogGroup = '/ecs/kimodo',
    [string]$Region = 'us-east-1'
)

$ErrorActionPreference = 'Continue'

function Get-TaskSg {
    $sg = aws cloudformation describe-stacks --stack-name $StackName --region $Region `
        --query "Stacks[0].Outputs[?OutputKey=='TaskSecurityGroupId'].OutputValue | [0]" `
        --output text 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $sg -or $sg -eq 'None') {
        throw "Khong doc duoc TaskSecurityGroupId tu stack $StackName. Stack da deploy chua?"
    }
    return $sg.Trim()
}

function Get-PublicSubnets {
    # Subnet cua ASG chinh la 2 public subnet (sync tu VvaVpcStack luc tao tay).
    $ids = aws autoscaling describe-auto-scaling-groups `
        --auto-scaling-group-names $AsgName --region $Region `
        --query 'AutoScalingGroups[0].VPCZoneIdentifier' --output text 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $ids -or $ids -eq 'None') {
        throw "Khong doc duoc subnets cua ASG $AsgName."
    }
    return $ids.Trim()
}

function Show-Status {
    $asg = aws autoscaling describe-auto-scaling-groups `
        --auto-scaling-group-names $AsgName --region $Region `
        --query 'AutoScalingGroups[0].{Desired:DesiredCapacity,Instances:Instances[].LifecycleState}' `
        --output json 2>$null | ConvertFrom-Json
    $cl = aws ecs describe-clusters --clusters $Cluster --region $Region `
        --query 'clusters[0].{registered:registeredContainerInstancesCount,running:runningTasksCount,pending:pendingTasksCount}' `
        --output json 2>$null | ConvertFrom-Json
    $tasks = aws ecs list-tasks --cluster $Cluster --region $Region `
        --query 'taskArns' --output json 2>$null | ConvertFrom-Json

    Write-Host "ASG $AsgName : Desired=$($asg.Desired) Instances=[$($asg.Instances -join ', ')]"
    Write-Host "Cluster $Cluster : registered=$($cl.registered) running=$($cl.running) pending=$($cl.pending)"
    if ($tasks -and $tasks.Count -gt 0) {
        foreach ($t in $tasks) { Write-Host "  task: $t" }
    } else {
        Write-Host "  task: (khong co)"
    }
    if ($asg.Desired -eq 1 -and ($null -eq $tasks -or $tasks.Count -eq 0)) {
        Write-Warning "May dang chay NHUNG khong co task = dang dot tien vo ich. Chay '.\\scripts\\kimodo-task.ps1 on' de run task, hoac 'off' de tat han."
    }
}

function Wait-AsgReady {
    param([int]$Tries = 20, [int]$SleepSec = 15)
    for ($i = 0; $i -lt $Tries; $i++) {
        $state = aws autoscaling describe-auto-scaling-groups `
            --auto-scaling-group-names $AsgName --region $Region `
            --query 'AutoScalingGroups[0].Instances[0].LifecycleState' `
            --output text 2>$null
        $reg = aws ecs describe-clusters --clusters $Cluster --region $Region `
            --query 'clusters[0].registeredContainerInstancesCount' `
            --output text 2>$null
        Write-Host "  doi may... ($i) ASG=$state registered=$reg"
        if ($state -eq 'InService' -and $reg -eq '1') { return $true }
        Start-Sleep -Seconds $SleepSec
    }
    return $false
}

function Wait-TaskRunning {
    param([string]$TaskArn, [int]$Tries = 20, [int]$SleepSec = 15)
    for ($i = 0; $i -lt $Tries; $i++) {
        $s = aws ecs describe-tasks --cluster $Cluster --region $Region --tasks $TaskArn `
            --query 'tasks[0].{last:lastStatus,cont:containers[0].lastStatus}' `
            --output text 2>$null
        Write-Host "  doi task... ($i) $s"
        if ($s -match 'RUNNING') { return $true }
        Start-Sleep -Seconds $SleepSec
    }
    return $false
}

switch ($Action) {
    'status' { Show-Status }

    'logs' {
        Write-Host "Duoi log $LogGroup (cho 'model loaded' la san sang):" -ForegroundColor Cyan
        aws logs tail $LogGroup --since 10m --region $Region 2>&1 | Select-Object -First 40
    }

    'on' {
        Write-Host "-> scale $AsgName ve 1" -ForegroundColor Cyan
        aws autoscaling set-desired-capacity --auto-scaling-group-name $AsgName `
            --desired-capacity 1 --region $Region
        if ($LASTEXITCODE -ne 0) { throw "set-desired-capacity that bai." }

        if (-not (Wait-AsgReady)) {
            throw "May chua InService/registered sau ~5 phut. Kiem tra: aws autoscaling describe-scaling-activities --auto-scaling-group-name $AsgName --max-records 3"
        }
        $inst = aws autoscaling describe-auto-scaling-groups `
            --auto-scaling-group-names $AsgName --region $Region `
            --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text
        Write-Host "   may san sang: $inst" -ForegroundColor Green

        $sg = Get-TaskSg
        $subnets = Get-PublicSubnets
        Write-Host "-> run-task $TaskFamily (sg=$sg subnets=$subnets)" -ForegroundColor Cyan
        # KHONG assignPublicIp: EC2 launch type se 400 neu co.
        $out = aws ecs run-task --cluster $Cluster --task-definition $TaskFamily --region $Region `
            --network-configuration "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$sg]}" `
            --query 'tasks[0].taskArn' --output text 2>&1
        if ($LASTEXITCODE -ne 0) { throw "run-task that bai: $out" }
        $taskArn = $out.Trim()
        Write-Host "   task: $taskArn"

        if (-not (Wait-TaskRunning -TaskArn $taskArn)) {
            throw "Task chua RUNNING sau ~5 phut. Kiem tra: aws ecs describe-tasks --cluster $Cluster --tasks $taskArn"
        }
        Write-Host "TASK RUNNING. Cold start ~5 phut nua (pull image + sync S3 17.8GB)." -ForegroundColor Green
        Write-Host "Kiem tra: .\\scripts\\kimodo-task.ps1 logs  (cho 'model loaded')"
    }

    'off' {
        $tasks = aws ecs list-tasks --cluster $Cluster --region $Region `
            --query 'taskArns' --output text 2>$null
        if ($tasks -and $tasks -ne 'None' -and $tasks.Trim()) {
            foreach ($t in ($tasks -split '\s+')) {
                if ($t.Trim()) {
                    Write-Host "-> stop task $t" -ForegroundColor Cyan
                    aws ecs stop-task --cluster $Cluster --task $t.Trim() --region $Region > $null 2>&1
                }
            }
        } else {
            Write-Host "Khong co task nao dang chay."
        }
        Write-Host "-> scale $AsgName ve 0 (het ~$24/ngay tien GPU)" -ForegroundColor Cyan
        aws autoscaling set-desired-capacity --auto-scaling-group-name $AsgName `
            --desired-capacity 0 --region $Region
        if ($LASTEXITCODE -ne 0) { throw "set-desired-capacity ve 0 that bai." }
        Write-Host "Da tat. Verify: .\\scripts\\kimodo-task.ps1 status" -ForegroundColor Green
    }
}
