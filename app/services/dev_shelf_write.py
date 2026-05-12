from __future__ import annotations


from app.services.dev_shelf_base import *
class DevShelfWriteMixin:

    def create_project(self, payload: DevShelfProjectCreateRequest) -> DevShelfProjectCreateResponse:
        intake = self._project_intake_from_payload(payload)
        intake_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                prefix="codex-workbench-intake-",
                delete=False,
            ) as fh:
                intake_path = Path(fh.name)
                json.dump(intake, fh, ensure_ascii=False, indent=2)
                fh.write("\n")

            result = self._run_dev_shelf_tool(
                "dev_shelf_start_project.py",
                [
                    "--intake",
                    str(intake_path),
                    "--root",
                    str(self.root),
                    "--pretty",
                ],
            )
        finally:
            if intake_path is not None:
                try:
                    intake_path.unlink(missing_ok=True)
                except OSError:
                    pass

        if not result.get("run_id"):
            raise DevShelfToolError("dev-shelf start project did not return run_id")
        return DevShelfProjectCreateResponse(**result)


    def create_project_directory(
        self,
        payload: DevShelfDirectoryCreateRequest,
    ) -> DevShelfDirectoryCreateResponse:
        parent_path = self._resolve_project_directory(payload.parent_path, must_exist=True)
        name = self._validate_new_directory_name(payload.name)
        candidate = (parent_path / name).resolve(strict=False)
        if not self._is_under_projects_root(candidate):
            raise DevShelfProjectConflict("目录必须创建在项目根目录内。")
        try:
            candidate.mkdir()
        except FileExistsError as exc:
            raise DevShelfProjectConflict(f"目录已存在：{candidate}") from exc
        except OSError as exc:
            raise DevShelfProjectConflict(f"目录创建失败：{candidate}") from exc
        return DevShelfDirectoryCreateResponse(path=str(candidate))


    def cancel_run(self, run_id: str, payload: DevShelfRunCancelRequest) -> DevShelfRunDetail:
        run_dir = self._run_dir(run_id)
        state = self._load_state(run_dir, run_id)
        if state.get("status") == "cancelled":
            return self.get_run(run_id)

        active = self._active_gateway_launch(run_id)
        if active is not None:
            self._mark_latest_gateway_abort_requested(run_id, active.gateway_session_id)
            self._stop_gateway_process(active.process)
            self._gateway_launches.pop(run_id, None)

        run_state_path = run_dir / "run-state.json"
        self._run_dev_shelf_tool(
            "dev_shelf_emit_event.py",
            [
                "stage",
                "--run-state",
                str(run_state_path),
                "--actor",
                "human",
                "--stage",
                str(state.get("current_stage") or "cancelled"),
                "--run-status",
                "cancelled",
                "--note",
                payload.note or "用户在网页终止任务。",
                "--apply",
                "--pretty",
            ],
        )
        return self.get_run(run_id)


    def continue_workflow(self, run_id: str) -> DevShelfRunDetail:
        run_dir = self._run_dir(run_id)
        latest_packet = self._latest_packet(run_dir)
        if latest_packet is None or latest_packet.decision_type != "enter_stage":
            raise DevShelfWorkflowConflict("当前下一步不是流程推进。")
        if not latest_packet.path:
            raise DevShelfWorkflowConflict("当前流程推进 packet 缺少路径。")

        self._run_dev_shelf_tool(
            "dev_shelf_workflow_action.py",
            [
                "--pretty",
                "continue-enter-stage",
                "--run-state",
                str(run_dir / "run-state.json"),
                "--packet",
                latest_packet.path,
                "--apply",
            ],
        )
        return self.get_run(run_id)


    def _artifact_is_registered(self, artifact: dict[str, Any] | None) -> bool:
        return bool(artifact and artifact.get("status") not in {None, "", "missing"})


    def _emit_artifact_done(
        self,
        *,
        run_state_path: Path,
        artifact_id: str,
        title: str,
        path: Path,
        note: str,
    ) -> None:
        self._run_dev_shelf_tool(
            "dev_shelf_emit_event.py",
            [
                "artifact",
                "--run-state",
                str(run_state_path),
                "--actor",
                "ai",
                "--artifact-id",
                artifact_id,
                "--artifact-status",
                "done",
                "--title",
                title,
                "--path",
                str(path),
                "--produced-by",
                "stage.execution",
                "--note",
                note,
                "--apply",
                "--pretty",
            ],
        )


    def register_gateway_result(
        self,
        run_id: str,
        payload: DevShelfGatewayRegisterResultRequest,
    ) -> DevShelfRunDetail:
        run_dir = self._run_dir(run_id)
        state = self._load_state(run_dir, run_id)
        existing_implementation = self._find_artifact(state, IMPLEMENTATION_RESULT_ARTIFACT_ID)
        existing_quick_deploy = self._find_artifact(state, QUICK_DEPLOY_GUIDE_ARTIFACT_ID)
        if self._artifact_is_registered(existing_implementation) and self._artifact_is_registered(existing_quick_deploy):
            return self.get_run(run_id)

        session_dir = self._gateway_session_dir(run_dir, payload.session_id)
        status = self._gateway_status_from_session(session_dir)
        if status.status != "completed":
            raise DevShelfGatewayConflict("pi-agent 还没有完成，不能登记实现结果。")

        latest_packet = self._latest_packet(run_dir)
        if not self._is_execution_gateway_result(status, latest_packet):
            raise DevShelfGatewayConflict("当前 Gateway session 不是 execution 阶段结果，不能登记为实现结果。")

        runtime_events_path = Path(status.runtime_events_path) if status.runtime_events_path else None
        if runtime_events_path is None or not runtime_events_path.is_file():
            raise DevShelfGatewayConflict("Gateway runtime events 不存在，无法生成实现结果。")

        implementation_path = self._implementation_result_path(run_dir, latest_packet, status)
        if implementation_path is None:
            raise DevShelfGatewayConflict("当前 execution packet 没有 implementation_result 输出路径。")
        quick_deploy_path = self._quick_deploy_guide_path(run_dir, latest_packet, status)

        messages, _event_count = self._read_gateway_transcript(runtime_events_path)

        run_state_path = run_dir / "run-state.json"
        if not self._artifact_is_registered(existing_implementation):
            content = self._render_gateway_implementation_result(
                run_id=run_id,
                status=status,
                messages=messages,
                target_path=implementation_path,
                note=payload.note,
                packet=latest_packet.content if latest_packet else None,
            )
            implementation_path.parent.mkdir(parents=True, exist_ok=True)
            implementation_path.write_text(content, encoding="utf-8")
            self._emit_artifact_done(
                run_state_path=run_state_path,
                artifact_id=IMPLEMENTATION_RESULT_ARTIFACT_ID,
                title="实现结果",
                path=implementation_path,
                note=payload.note or "Workbench 已将本轮 pi-agent 执行结果登记为 implementation_result。",
            )

        if quick_deploy_path is not None and not self._artifact_is_registered(existing_quick_deploy):
            content = self._render_gateway_quick_deploy_guide(
                run_id=run_id,
                status=status,
                messages=messages,
                target_path=quick_deploy_path,
                packet=latest_packet.content if latest_packet else None,
            )
            quick_deploy_path.parent.mkdir(parents=True, exist_ok=True)
            quick_deploy_path.write_text(content, encoding="utf-8")
            self._emit_artifact_done(
                run_state_path=run_state_path,
                artifact_id=QUICK_DEPLOY_GUIDE_ARTIFACT_ID,
                title="快速部署文档",
                path=quick_deploy_path,
                note="Workbench 已将本轮 pi-agent 快速部署说明登记为 quick_deploy_guide。",
            )

        self._write_next_packet(run_state_path)
        return self.get_run(run_id)


    def confirm_gateway_candidate(
        self,
        run_id: str,
        candidate_id: str,
        payload: DevShelfGatewayCandidateConfirmRequest,
    ) -> DevShelfRunDetail:
        if not CANDIDATE_ID_RE.fullmatch(candidate_id):
            raise DevShelfGatewayConflict(f"Invalid Gateway candidate id: {candidate_id}")

        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, payload.session_id)
        status = self._gateway_status_from_session(session_dir)
        if status.status != "completed":
            raise DevShelfGatewayConflict("pi-agent 未成功完成，不能确认本轮候选产物。")
        candidate_path = self._gateway_candidate_path(session_dir)
        candidate_payload = self._load_json(candidate_path)
        if not isinstance(candidate_payload, dict):
            raise DevShelfGatewayConflict("Gateway candidate file is invalid.")
        candidate = self._gateway_candidate_from_file(candidate_path, candidate_id)
        event = candidate.get("event") if isinstance(candidate.get("event"), dict) else None
        if not event or not event.get("artifact_id"):
            raise DevShelfGatewayConflict(f"Gateway candidate is not confirmable: {candidate_id}")

        run_state_path = run_dir / "run-state.json"
        applied_candidate_ids: set[str] = set()
        selected_requires_review = bool(candidate.get("review_required"))

        if selected_requires_review:
            self._apply_gateway_candidate(
                candidate_path=candidate_path,
                candidate_id=candidate_id,
                run_state_path=run_state_path,
            )
            applied_candidate_ids.add(candidate_id)
            artifact = {
                "title": event.get("title"),
                "path": event.get("path"),
                "produced_by": event.get("produced_by"),
            }
            self._emit_artifact_decision(
                run_state_path=run_state_path,
                artifact_id=str(event["artifact_id"]),
                artifact_status="approved",
                artifact=artifact,
                note=payload.decision_note or f"{event.get('title') or event['artifact_id']} 已在网页确认。",
            )
            self._advance_after_confirmed_gateway_candidate(
                run_state_path=run_state_path,
                artifact_id=str(event["artifact_id"]),
            )

        self._apply_auto_gateway_candidates(
            candidate_path=candidate_path,
            candidate_payload=candidate_payload,
            run_state_path=run_state_path,
            skip_candidate_ids=applied_candidate_ids,
        )
        self._write_next_packet(run_state_path)
        return self.get_run(run_id)


    def revise_gateway_candidate(
        self,
        run_id: str,
        candidate_id: str,
        payload: DevShelfGatewayCandidateReviseRequest,
    ) -> DevShelfRunDetail:
        if not CANDIDATE_ID_RE.fullmatch(candidate_id):
            raise DevShelfGatewayConflict(f"Invalid Gateway candidate id: {candidate_id}")
        feedback = payload.feedback.strip()
        if not feedback:
            raise DevShelfGatewayConflict("修改意见不能为空。")
        if self._active_gateway_launch(run_id) is not None:
            raise DevShelfGatewayConflict("pi-agent 正在运行，请先中止后再提交修改意见。")

        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, payload.session_id)
        status = self._gateway_status_from_session(session_dir)
        if status.status != "completed":
            raise DevShelfGatewayConflict("pi-agent 未成功完成，不能基于本轮候选产物提交修改意见。")
        candidate_path = self._gateway_candidate_path(session_dir)
        candidate = self._gateway_candidate_from_file(candidate_path, candidate_id)
        event = candidate.get("event") if isinstance(candidate.get("event"), dict) else None
        if not event or not event.get("artifact_id"):
            raise DevShelfGatewayConflict(f"Gateway candidate is not revisable: {candidate_id}")

        run_state_path = run_dir / "run-state.json"
        self._run_dev_shelf_tool(
            "dev_shelf_workflow_action.py",
            [
                "--pretty",
                "apply-candidate",
                "--candidate",
                str(candidate_path),
                "--candidate-id",
                candidate_id,
                "--run-state",
                str(run_state_path),
                "--apply",
            ],
        )
        refreshed_state = self._load_state(run_dir, run_id)
        refreshed_artifact = self._find_artifact(refreshed_state, str(event["artifact_id"])) or {}
        artifact = {
            "title": event.get("title"),
            "path": refreshed_artifact.get("path") or event.get("path"),
            "produced_by": refreshed_artifact.get("produced_by") or event.get("produced_by"),
        }
        artifact_revision_id = self._artifact_revision_id(refreshed_artifact)
        feedback_path = self._write_artifact_feedback(
            run_dir=run_dir,
            run_id=run_id,
            artifact_id=str(event["artifact_id"]),
            artifact_path=str(artifact.get("path") or ""),
            artifact_revision_id=artifact_revision_id,
            candidate_id=candidate_id,
            feedback=feedback,
        )
        self._emit_artifact_decision(
            run_state_path=run_state_path,
            artifact_id=str(event["artifact_id"]),
            artifact_status="rejected",
            artifact=artifact,
            note=f"用户要求修订并重新生成。反馈记录：{feedback_path}。修改意见：{feedback}",
            feedback_path=str(feedback_path),
            artifact_revision_id=artifact_revision_id,
        )
        self._write_next_packet(run_state_path)
        return self.get_run(run_id)


    def decide_human_gate(
        self,
        run_id: str,
        gate_id: str,
        payload: DevShelfHumanGateDecisionRequest,
    ) -> DevShelfRunDetail:
        if not GATE_ID_RE.fullmatch(gate_id):
            raise DevShelfGateConflict(f"Invalid gate id: {gate_id}")

        run_dir = self._run_dir(run_id)
        state = self._load_state(run_dir, run_id)
        pending_gates = self._pending_human_gates(state)
        gate = next((item for item in pending_gates if item.gate_id == gate_id), None)
        if gate is None:
            known_gate = self._find_gate(state, gate_id)
            if known_gate is None:
                raise DevShelfGateConflict(f"Human gate not found or not pending: {gate_id}")
            raise DevShelfGateConflict(f"Human gate is not pending: {gate_id}")

        artifact_id = gate.artifact_id or self._artifact_id_for_gate(state, gate_id)
        run_state_path = run_dir / "run-state.json"
        decision_note = payload.decision_note or self._default_decision_note(gate_id, payload.decision)

        if artifact_id:
            artifact = self._find_artifact(state, artifact_id) or {}
            self._emit_artifact_decision(
                run_state_path=run_state_path,
                artifact_id=artifact_id,
                artifact_status=payload.decision,
                artifact=artifact,
                note=decision_note,
            )
            if payload.decision == "rejected":
                refreshed_state = self._load_state(run_dir, run_id)
                refreshed_gate = self._find_gate(refreshed_state, gate_id) or {}
                self._emit_gate_decision(
                    run_state_path=run_state_path,
                    gate_id=gate_id,
                    gate_status="rejected",
                    gate=refreshed_gate or gate.model_dump(),
                    note=decision_note,
                )
        else:
            self._emit_gate_decision(
                run_state_path=run_state_path,
                gate_id=gate_id,
                gate_status=payload.decision,
                gate=gate.model_dump(),
                note=decision_note,
            )

        self._write_next_packet(run_state_path)
        return self.get_run(run_id)


    def revise_artifact(
        self,
        run_id: str,
        artifact_id: str,
        payload: DevShelfArtifactReviseRequest,
    ) -> DevShelfRunDetail:
        if not GATE_ID_RE.fullmatch(artifact_id):
            raise DevShelfGateConflict(f"Invalid artifact id: {artifact_id}")
        feedback = payload.feedback.strip()
        if not feedback:
            raise DevShelfGateConflict("修改意见不能为空。")
        if self._active_gateway_launch(run_id) is not None:
            raise DevShelfGatewayConflict("pi-agent 正在运行，请先中止后再提交修改意见。")

        run_dir = self._run_dir(run_id)
        state = self._load_state(run_dir, run_id)
        artifact = self._find_artifact(state, artifact_id)
        if artifact is None:
            raise DevShelfGateConflict(f"Artifact not found: {artifact_id}")

        pending_gates = self._pending_human_gates(state)
        gate = next((item for item in pending_gates if item.artifact_id == artifact_id), None)
        if gate is None and artifact.get("status") not in {"draft", "in_review"}:
            raise DevShelfGateConflict(f"Artifact is not waiting for revision: {artifact_id}")

        run_state_path = run_dir / "run-state.json"
        artifact_revision_id = self._artifact_revision_id(artifact)
        feedback_path = self._write_artifact_feedback(
            run_dir=run_dir,
            run_id=run_id,
            artifact_id=artifact_id,
            artifact_path=str(artifact.get("path") or ""),
            artifact_revision_id=artifact_revision_id,
            candidate_id=None,
            feedback=feedback,
        )
        decision_note = f"用户要求修订并重新生成。反馈记录：{feedback_path}。修改意见：{feedback}"
        self._emit_artifact_decision(
            run_state_path=run_state_path,
            artifact_id=artifact_id,
            artifact_status="rejected",
            artifact=artifact,
            note=decision_note,
            feedback_path=str(feedback_path),
            artifact_revision_id=artifact_revision_id,
        )
        if gate is not None:
            refreshed_state = self._load_state(run_dir, run_id)
            refreshed_gate = self._find_gate(refreshed_state, gate.gate_id) or gate.model_dump()
            self._emit_gate_decision(
                run_state_path=run_state_path,
                gate_id=gate.gate_id,
                gate_status="rejected",
                gate=refreshed_gate,
                note=decision_note,
            )
        self._write_next_packet(run_state_path)
        return self.get_run(run_id)


    def _is_execution_gateway_result(
        self,
        status: DevShelfGatewaySessionStatus,
        latest_packet: DevShelfExecutionPacket | None,
    ) -> bool:
        if status.packet_target != "stage.execution":
            return False
        if latest_packet is None or not latest_packet.path or not status.packet_path:
            return False
        try:
            return Path(status.packet_path).resolve() == Path(latest_packet.path).resolve()
        except OSError:
            return False


    def _implementation_result_path(
        self,
        run_dir: Path,
        latest_packet: DevShelfExecutionPacket | None,
        status: DevShelfGatewaySessionStatus,
    ) -> Path | None:
        return self._artifact_output_path(
            run_dir=run_dir,
            latest_packet=latest_packet,
            status=status,
            artifact_id=IMPLEMENTATION_RESULT_ARTIFACT_ID,
            fallback_filename="implementation-result.md",
        )


    def _quick_deploy_guide_path(
        self,
        run_dir: Path,
        latest_packet: DevShelfExecutionPacket | None,
        status: DevShelfGatewaySessionStatus,
    ) -> Path | None:
        return self._artifact_output_path(
            run_dir=run_dir,
            latest_packet=latest_packet,
            status=status,
            artifact_id=QUICK_DEPLOY_GUIDE_ARTIFACT_ID,
            fallback_filename="quick-deploy-guide.md",
        )


    def _artifact_output_path(
        self,
        *,
        run_dir: Path,
        latest_packet: DevShelfExecutionPacket | None,
        status: DevShelfGatewaySessionStatus,
        artifact_id: str,
        fallback_filename: str,
    ) -> Path | None:
        raw_path = self._artifact_output_path_from_packet(latest_packet.content if latest_packet else None, artifact_id)
        if raw_path is None and status.packet_path:
            packet_payload = self._load_json(Path(status.packet_path))
            raw_path = self._artifact_output_path_from_packet(packet_payload, artifact_id)
        if raw_path is None:
            raw_path = self._infer_artifact_output_path(run_dir, fallback_filename)
        if raw_path is None:
            return None

        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise DevShelfGatewayConflict(f"{artifact_id} 路径必须位于 dev-shelf 根目录内。") from exc
        return resolved


    def _artifact_output_path_from_packet(self, content: dict[str, Any] | None, artifact_id: str) -> str | None:
        if not isinstance(content, dict):
            return None
        outputs = content.get("pending_outputs")
        if not isinstance(outputs, list):
            outputs = content.get("outputs_to_produce")
        if not isinstance(outputs, list):
            return None
        for item in outputs:
            if not isinstance(item, dict) or item.get("artifact_id") != artifact_id:
                continue
            raw_path = item.get("path") or item.get("current_path")
            if raw_path:
                return str(raw_path)
            state_event = item.get("state_event_on_draft")
            if isinstance(state_event, dict) and state_event.get("path"):
                return str(state_event["path"])
        return None


    def _infer_artifact_output_path(self, run_dir: Path, filename: str) -> str | None:
        state = self._load_json(run_dir / "run-state.json") or {}
        for artifact in state.get("artifacts", []):
            if not isinstance(artifact, dict) or not artifact.get("path"):
                continue
            path = Path(str(artifact["path"]))
            if not path.is_absolute():
                path = self.root / path
            try:
                resolved = path.resolve(strict=False)
                resolved.relative_to(self.root / "docs")
            except (OSError, ValueError):
                continue
            return str(resolved.parent / filename)
        return None


    def _render_gateway_implementation_result(
        self,
        *,
        run_id: str,
        status: DevShelfGatewaySessionStatus,
        messages: list[DevShelfGatewayTranscriptMessage],
        target_path: Path,
        note: str | None,
        packet: dict[str, Any] | None = None,
    ) -> str:
        written_files = self._written_files_from_transcript(messages)
        final_message = self._final_gateway_message_text(messages)
        evidence = self._collect_gateway_execution_evidence(packet, final_message)
        model_label = " / ".join(item for item in [status.provider, status.model] if item)
        lines = [
            "# 实现结果",
            "",
            "## 登记信息",
            "",
            f"- run_id: `{run_id}`",
            f"- gateway_session_id: `{status.gateway_session_id or '-'}`",
            f"- packet: `{status.packet_path or '-'}`",
            f"- model: `{model_label or '-'}`",
        ]
        if status.finished_at:
            lines.append(f"- finished_at: `{status.finished_at}`")
        lines.append(f"- artifact_path: `{target_path}`")
        if note:
            lines.extend(["", "## 备注", "", note.strip()])

        evidence_lines = self._render_gateway_execution_evidence_markdown(evidence)
        if evidence_lines:
            lines.extend([""])
            lines.extend(evidence_lines)

        if written_files:
            lines.extend(["", "## 修改文件", ""])
            for path in written_files:
                lines.append(f"- `{path}`")
        else:
            lines.extend(["", "## 修改文件", "", "- 未从运行对话中识别到写入文件。"])

        lines.extend(["", "## pi-agent 最终回复", ""])
        lines.append(final_message or "未捕获到最终回复。")
        return "\n".join(lines).rstrip() + "\n"


    def _render_gateway_quick_deploy_guide(
        self,
        *,
        run_id: str,
        status: DevShelfGatewaySessionStatus,
        messages: list[DevShelfGatewayTranscriptMessage],
        target_path: Path,
        packet: dict[str, Any] | None = None,
    ) -> str:
        final_message = self._final_gateway_message_text(messages)
        evidence = self._collect_gateway_execution_evidence(packet, final_message)
        project_path = evidence.get("project_path")
        services = self._running_services_from_runtime_events(status)
        lines = [
            "# 快速部署文档",
            "",
            "## 登记信息",
            "",
            f"- run_id: `{run_id}`",
            f"- gateway_session_id: `{status.gateway_session_id or '-'}`",
            f"- status: `{status.status}`",
        ]
        if status.provider or status.model:
            lines.append(f"- model: `{' / '.join(item for item in [status.provider, status.model] if item)}`")
        if status.finished_at:
            lines.append(f"- finished_at: `{status.finished_at}`")
        lines.extend(
            [
                f"- artifact_path: `{target_path}`",
                f"- project_path: `{project_path or '-'}`",
                "",
                "## 环境准备",
                "",
            ]
        )
        lines.extend(self._quick_deploy_environment_lines(project_path))
        lines.extend(["", "## 快速启动 / 部署", ""])
        lines.extend(self._quick_deploy_start_lines(project_path))
        lines.extend(["", "## 本轮运行服务", ""])
        if services:
            for service in services:
                lines.extend(self._running_service_markdown(service))
        else:
            lines.append("- 本轮未捕获到 pi-agent 明确报告的运行服务。")
        lines.extend(
            [
                "",
                "## 验证入口",
                "",
                "- 优先按 implementation_result 中的验证命令复查。",
                "- 如本文件列出本轮运行服务，可直接访问对应 `url`；浏览器地址应使用 127.0.0.1 或 localhost。",
                "- 若服务未启动，请先在对应工作目录运行上方启动命令。",
                "",
                "## pi-agent 最终回复",
                "",
                final_message or "未捕获到最终回复。",
                "",
                "## 说明",
                "",
                "- Workbench 不再根据项目目录自动猜测预览地址；只有 pi-agent 明确报告的运行服务才会展示为可直接预览入口。",
                "- 本文件由 Workbench 根据本轮 Gateway transcript 和 packet 生成，并登记为 quick_deploy_guide。",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"


    def _running_services_from_runtime_events(self, status: DevShelfGatewaySessionStatus) -> list[dict[str, Any]]:
        if not status.runtime_events_path:
            return []
        helper = self._load_artifact_result_helper("collect_running_services")
        if helper is None:
            return []
        try:
            services = helper(Path(status.runtime_events_path))
        except Exception:
            return []
        if not isinstance(services, list):
            return []
        return [item for item in services if isinstance(item, dict)]


    def _quick_deploy_environment_lines(self, project_path: Any) -> list[str]:
        path = self._normalized_existing_path(project_path)
        if path is None:
            return ["- 未记录 project_path；请以 execution_todo_json 和项目 README 为准。"]
        lines = [f"- 工作目录：`{path}`"]
        package_json = self._project_package_json(path)
        if package_json is not None:
            package_manager = self._project_package_manager(path)
            install_command = {
                "pnpm": "pnpm install",
                "yarn": "yarn install",
                "bun": "bun install",
            }.get(package_manager, "npm install")
            lines.extend(["- 检测到 Node.js 项目：`package.json`", f"- 建议安装依赖：`{install_command}`"])
        if (path / "requirements.txt").is_file():
            lines.append("- 检测到 Python requirements：`python3 -m pip install -r requirements.txt`")
        if (path / "pyproject.toml").is_file():
            lines.append("- 检测到 Python pyproject：按项目工具链运行 `pip install -e .` 或对应包管理命令。")
        if len(lines) == 1:
            lines.append("- 未检测到标准依赖清单；请以项目 README 或 pi-agent 最终回复为准。")
        return lines


    def _quick_deploy_start_lines(self, project_path: Any) -> list[str]:
        path = self._normalized_existing_path(project_path)
        if path is None:
            return ["- 未能生成启动命令；请查看项目 README 和 pi-agent 最终回复。"]
        package_json = self._project_package_json(path)
        if package_json is not None:
            scripts = package_json.get("scripts") if isinstance(package_json.get("scripts"), dict) else {}
            package_manager = self._project_package_manager(path)
            lines = [
                f"- `{self._project_script_command(package_manager, script_name)}`"
                for script_name in ["dev", "start", "preview", "build", "test"]
                if isinstance(scripts.get(script_name), str)
            ]
            if lines:
                return lines
        if (path / "index.html").is_file():
            return ["- `python3 -m http.server 4173 --bind 127.0.0.1`"]
        return ["- 未检测到标准启动脚本；请查看项目 README 和 pi-agent 最终回复。"]


    def _running_service_markdown(self, service: dict[str, Any]) -> list[str]:
        lines = [f"### {service.get('service_name') or '运行服务'}", ""]
        for key in ["kind", "url", "port", "command", "cwd", "log_path", "source"]:
            value = service.get(key)
            if value not in {None, ""}:
                lines.append(f"- {key}: `{value}`")
        lines.append("")
        return lines


    def _normalized_existing_path(self, raw_path: Any) -> Path | None:
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        try:
            path = Path(raw_path).expanduser().resolve(strict=False)
        except OSError:
            return None
        return path if path.exists() and path.is_dir() else None


    def _project_package_json(self, project_path: Path) -> dict[str, Any] | None:
        package_json_path = project_path / "package.json"
        if not package_json_path.is_file():
            return None
        try:
            payload = json.loads(package_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None


    def _project_package_manager(self, project_path: Path) -> str:
        if (project_path / "pnpm-lock.yaml").is_file():
            return "pnpm"
        if (project_path / "yarn.lock").is_file():
            return "yarn"
        if (project_path / "bun.lockb").is_file() or (project_path / "bun.lock").is_file():
            return "bun"
        return "npm"


    def _project_script_command(self, package_manager: str, script_name: str) -> str:
        if package_manager == "yarn":
            return f"yarn run {script_name}"
        if package_manager == "bun":
            return f"bun run {script_name}"
        return f"{package_manager} run {script_name}"


    def _collect_gateway_execution_evidence(
        self,
        packet: dict[str, Any] | None,
        final_message: str | None,
    ) -> dict[str, Any]:
        helper = self._load_artifact_result_helper("collect_execution_evidence")
        if helper is not None and isinstance(packet, dict):
            try:
                return helper(packet, final_reply=final_message)
            except Exception:
                pass
        project_path = None
        if isinstance(packet, dict):
            workspace = packet.get("workspace") if isinstance(packet.get("workspace"), dict) else {}
            runtime = (
                packet.get("agent_runtime_contract")
                if isinstance(packet.get("agent_runtime_contract"), dict)
                else {}
            )
            project_path = workspace.get("project_path") or runtime.get("cwd")
        return {
            "schema_version": "1.0",
            "project_path": str(project_path) if project_path else None,
            "status": "collector_unavailable",
            "changed_files": [],
            "git": {
                "available": False,
                "inside_work_tree": False,
                "status_short": [],
                "diff_stat": "",
                "cached_diff_stat": "",
                "error": "dev-shelf artifact_result helper unavailable",
            },
            "verification": {
                "status": "not_captured",
                "commands": [],
                "results": [],
                "note": "未能加载 dev-shelf 证据采集器；不要视为验证通过。",
            },
            "unfinished": [],
            "notes": ["证据缺失：Workbench 未能加载 dev-shelf 证据采集器。"],
        }


    def _render_gateway_execution_evidence_markdown(self, evidence: dict[str, Any]) -> list[str]:
        helper = self._load_artifact_result_helper("render_execution_evidence_markdown")
        if helper is not None:
            try:
                rendered = helper(evidence)
                if isinstance(rendered, list):
                    return [str(item) for item in rendered]
            except Exception:
                pass
        status = evidence.get("status") or "unknown"
        project_path = evidence.get("project_path") or "-"
        return [
            "## 可验证证据",
            "",
            f"- evidence_status: `{status}`",
            f"- project_path: `{project_path}`",
            "- 证据缺失：未能渲染 dev-shelf 证据摘要；请查看 Gateway transcript。",
        ]


    def _load_artifact_result_helper(self, helper_name: str):
        for root in [self.tools_root, DEFAULT_DEV_SHELF_ROOT]:
            if not (root / "dev_shelf_gateway" / "artifact_result.py").is_file():
                continue
            inserted = False
            root_str = str(root)
            if root_str not in sys.path:
                sys.path.insert(0, root_str)
                inserted = True
            try:
                from dev_shelf_gateway import artifact_result

                helper = getattr(artifact_result, helper_name, None)
                if helper is not None:
                    return helper
            except (ImportError, ModuleNotFoundError, AttributeError):
                continue
            finally:
                if inserted:
                    try:
                        sys.path.remove(root_str)
                    except ValueError:
                        pass
        return None


    def _written_files_from_transcript(self, messages: list[DevShelfGatewayTranscriptMessage]) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        for message in messages:
            for match in re.finditer(r"Successfully wrote \d+ bytes to ([^\s]+)", message.text or ""):
                path = match.group(1)
                if path not in seen:
                    seen.add(path)
                    paths.append(path)
        return paths


    def _final_gateway_message_text(self, messages: list[DevShelfGatewayTranscriptMessage]) -> str | None:
        for role in ["assistant", "tool", "system", "error"]:
            for message in reversed(messages):
                text = (message.text or "").strip()
                if role == "assistant" and message.kind != "message":
                    continue
                if message.role == role and text:
                    return text
        return None


    def _project_intake_from_payload(self, payload: DevShelfProjectCreateRequest) -> dict[str, Any]:
        project_name = payload.project_name.strip()
        requirement = payload.requirement.strip()
        if not project_name:
            raise DevShelfProjectConflict("项目名不能为空。")
        if not requirement:
            raise DevShelfProjectConflict("需求不能为空。")

        project_slug = self._normalize_project_slug(payload.project_slug or project_name)
        request_summary = (payload.request_summary or "").strip() or self._summarize_requirement(requirement)
        intake: dict[str, Any] = {
            "schema_version": "1.0",
            "project_name": project_name,
            "project_slug": project_slug,
            "request_summary": request_summary,
            "requirement_draft": requirement,
            "task_type": payload.task_type,
            "task_type_status": payload.task_type_status,
            "project_context": payload.project_context,
            "requires_existing_project_analysis": payload.project_context == "existing_project",
            "mode": "standard",
        }

        workspace = self._workspace_from_payload(payload, project_slug=project_slug)
        if workspace is not None:
            intake["workspace"] = workspace
        return intake


    def _normalize_project_slug(self, raw_value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw_value.strip().lower())
        slug = re.sub(r"_+", "_", slug).strip("_-")
        if not slug:
            slug = f"web_project_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        if not PROJECT_SLUG_RE.fullmatch(slug):
            raise DevShelfProjectConflict(f"Invalid project slug: {raw_value!r}")
        return slug


    def _summarize_requirement(self, requirement: str) -> str:
        first_line = next((line.strip() for line in requirement.splitlines() if line.strip()), "")
        if not first_line:
            return "网页创建的 dev-shelf 任务"
        return first_line[:120]


    def _workspace_from_payload(
        self,
        payload: DevShelfProjectCreateRequest,
        *,
        project_slug: str,
    ) -> dict[str, Any] | None:
        raw_project_path = (payload.project_path or "").strip()
        if not raw_project_path and payload.project_context == "new_project":
            raw_project_path = str(self.root.parent / project_slug)
        if not raw_project_path:
            return None

        project_path = str(Path(raw_project_path).expanduser().resolve(strict=False))
        workspace: dict[str, Any] = {
            "kind": payload.project_context,
            "project_path": project_path,
            "allow_create_project_dir": payload.allow_create_project_dir,
            "allowed_read_paths": [project_path],
            "allowed_write_paths": [project_path] if payload.workspace_confirmed else [],
            "confirmation_status": "confirmed" if payload.workspace_confirmed else "unconfirmed",
        }
        if payload.project_context == "existing_project":
            workspace["existing_project_path"] = project_path
        if payload.project_context == "new_project":
            workspace["root_path"] = str(Path(project_path).parent)
        if payload.workspace_confirmed:
            workspace["confirmed_by"] = "human"
            workspace["confirmed_at"] = self._utc_now()
            workspace["note"] = "用户在网页创建 run 时确认 workspace。"
        return workspace


    def _write_artifact_feedback(
        self,
        *,
        run_dir: Path,
        run_id: str,
        artifact_id: str,
        artifact_path: str,
        artifact_revision_id: str,
        candidate_id: str | None,
        feedback: str,
    ) -> Path:
        feedback_dir = run_dir / "artifacts" / "workbench-feedback"
        feedback_dir.mkdir(parents=True, exist_ok=True)
        safe_artifact_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", artifact_id).strip("-") or "artifact"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        path = feedback_dir / f"{timestamp}-{safe_artifact_id}.md"
        created_at = self._utc_now()
        lines = [
            "---",
            f"run_id: {run_id}",
            f"artifact_id: {artifact_id}",
            f"artifact_path: {artifact_path or '-'}",
            f"artifact_revision_id: {artifact_revision_id}",
            f"candidate_id: {candidate_id or '-'}",
            f"created_at: {created_at}",
            "---",
            "",
            "# Workbench 修改意见",
            "",
            f"- run_id: `{run_id}`",
            f"- artifact_id: `{artifact_id}`",
            f"- artifact_path: `{artifact_path or '-'}`",
            f"- artifact_revision_id: `{artifact_revision_id}`",
            f"- candidate_id: `{candidate_id or '-'}`",
            f"- created_at: `{created_at}`",
            "",
            "## 反馈",
            "",
            feedback,
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path


    def _emit_artifact_decision(
        self,
        *,
        run_state_path: Path,
        artifact_id: str,
        artifact_status: str,
        artifact: dict[str, Any],
        note: str,
        feedback_path: str | None = None,
        artifact_revision_id: str | None = None,
    ) -> None:
        args = [
            "artifact",
            "--run-state",
            str(run_state_path),
            "--actor",
            "human",
            "--artifact-id",
            artifact_id,
            "--artifact-status",
            artifact_status,
            "--note",
            note,
            "--apply",
            "--pretty",
        ]
        if artifact.get("title"):
            args.extend(["--title", str(artifact["title"])])
        if artifact.get("path"):
            args.extend(["--path", str(artifact["path"])])
        if artifact.get("produced_by"):
            args.extend(["--produced-by", str(artifact["produced_by"])])
        if feedback_path:
            args.extend(["--feedback-path", feedback_path])
        if artifact_revision_id:
            args.extend(["--artifact-revision-id", artifact_revision_id])
        self._run_dev_shelf_tool("dev_shelf_emit_event.py", args)


    def _emit_gate_decision(
        self,
        *,
        run_state_path: Path,
        gate_id: str,
        gate_status: str,
        gate: dict[str, Any],
        note: str,
    ) -> None:
        args = [
            "gate",
            "--run-state",
            str(run_state_path),
            "--actor",
            "human",
            "--gate-id",
            gate_id,
            "--gate-status",
            gate_status,
            "--decision-note",
            note,
            "--apply",
            "--pretty",
        ]
        if gate.get("label"):
            args.extend(["--label", str(gate["label"])])
        if gate.get("required_for_stage"):
            args.extend(["--required-for-stage", str(gate["required_for_stage"])])
        if gate.get("owner"):
            args.extend(["--owner", str(gate["owner"])])
        self._run_dev_shelf_tool("dev_shelf_emit_event.py", args)


    def _advance_after_confirmed_gateway_candidate(self, *, run_state_path: Path, artifact_id: str) -> None:
        stage_advances = {
            "requirement_confirmation_checklist": {
                "stage": "confirmed_requirement",
                "run_status": "ready_for_next_stage",
                "next_allowed": ["skill_selection", "spec_drafting"],
                "note": "需求确认清单已确认，进入已确认需求阶段。",
            },
            "implementation_plan": {
                "stage": "spec_drafting",
                "run_status": "in_progress",
                "next_allowed": [],
                "note": "书架推进计划已确认，进入 spec 起草阶段。",
            },
            "spec": {
                "stage": "reuse_decision",
                "run_status": "in_progress",
                "next_allowed": [],
                "note": "spec 已确认，进入复用判断阶段。",
            },
            "reuse_decision": {
                "stage": "implementation_planning",
                "run_status": "in_progress",
                "next_allowed": [],
                "note": "复用判断已确认，进入执行待办规划阶段。",
            },
        }
        advance = stage_advances.get(artifact_id)
        if advance is None:
            return
        args = [
            "stage",
            "--run-state",
            str(run_state_path),
            "--actor",
            "human",
            "--stage",
            str(advance["stage"]),
            "--run-status",
            str(advance["run_status"]),
            "--note",
            str(advance["note"]),
            "--apply",
            "--pretty",
        ]
        next_allowed = advance.get("next_allowed")
        if next_allowed:
            args[args.index("--note") : args.index("--note")] = [
                "--next-allowed",
                *[str(item) for item in next_allowed],
            ]
        self._run_dev_shelf_tool(
            "dev_shelf_emit_event.py",
            args,
        )


    def _apply_gateway_candidate(self, *, candidate_path: Path, candidate_id: str, run_state_path: Path) -> None:
        self._run_dev_shelf_tool(
            "dev_shelf_workflow_action.py",
            [
                "--pretty",
                "apply-candidate",
                "--candidate",
                str(candidate_path),
                "--candidate-id",
                candidate_id,
                "--run-state",
                str(run_state_path),
                "--apply",
            ],
        )


    def _apply_auto_gateway_candidates(
        self,
        *,
        candidate_path: Path,
        candidate_payload: dict[str, Any],
        run_state_path: Path,
        skip_candidate_ids: set[str],
    ) -> None:
        candidates = candidate_payload.get("candidates")
        if not isinstance(candidates, list):
            return
        for item in candidates:
            if not isinstance(item, dict):
                continue
            candidate_id = item.get("candidate_id")
            if not candidate_id or candidate_id in skip_candidate_ids:
                continue
            if item.get("review_required"):
                continue
            event = item.get("event")
            source_output = item.get("source_output")
            if not isinstance(event, dict) or not event.get("artifact_id"):
                continue
            if isinstance(source_output, dict) and source_output.get("produced") is False:
                continue
            self._apply_gateway_candidate(
                candidate_path=candidate_path,
                candidate_id=str(candidate_id),
                run_state_path=run_state_path,
            )
            skip_candidate_ids.add(str(candidate_id))


    def _write_next_packet(self, run_state_path: Path) -> None:
        self._run_dev_shelf_tool(
            "dev_shelf_runner.py",
            [
                "--run-state",
                str(run_state_path),
                "--write-packet-pair",
                "--pretty",
            ],
        )
